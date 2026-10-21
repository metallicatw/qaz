from __future__ import annotations

import re
from collections import Counter
from datetime import timedelta
from html import escape as e
from statistics import median

from .config import Category
from .db import utcnow

STOP = set("""a an the and or for with of to in on by your you my our from at as is it this that personalized personalised
custom customizable modern simple cute funny best gift gifts design designs new name names photo text template editable
elegant minimalist minimal script""".split())
PRICE = re.compile(r"(\d[\d,]*\.?\d*)")


def _tokens(title: str, drop: set[str]) -> list[str]:
    words = [w for w in re.findall(r"[a-z][a-z0-9'&-]+", title.lower()) if w not in STOP and w not in drop and len(w) > 2]
    return words + [f"{a} {b}" for a, b in zip(words, words[1:])]


def _price(p: str | None) -> float | None:
    m = PRICE.search(p or "")
    try:
        return float(m.group(1).replace(",", "")) if m else None
    except ValueError:
        return None


def analyze(con, settings: dict, cats: list[Category], days_back: int = 28) -> list[dict]:
    from .perf import category_boost, category_commission
    today = utcnow().date()
    boost = category_boost(con, settings, cats)
    com = category_commission(con, cats, 90)
    since = (today - timedelta(days=90)).isoformat()
    out = []
    for c in cats:
        rows = [dict(r) for r in con.execute(
            """SELECT p.*, r.rank FROM rankings r JOIN products p USING(product_id)
               WHERE r.category=? AND p.alive=1 ORDER BY r.rank""", (c.key,))]
        if not rows:
            continue
        top = rows[:120]
        drop = set(re.findall(r"[a-z]+", c.qs.lower())) | {w.rstrip("s") for w in re.findall(r"[a-z]+", c.qs.lower())}
        top_tokens = Counter(t for r in top for t in set(_tokens(r["title"] or "", drop)))
        rest_tokens = Counter(t for r in rows[120:] for t in set(_tokens(r["title"] or "", drop)))
        n_top, n_rest = max(len(top), 1), max(len(rows) - len(top), 1)
        lift = []
        for t, k in top_tokens.items():
            if k >= 3:
                share_top, share_rest = k / n_top, (rest_tokens.get(t, 0) + 1) / (n_rest + 2)
                lift.append((t, k, round(share_top / share_rest, 2)))
        lift.sort(key=lambda x: (-x[2] * min(x[1], 12), -x[1]))
        stores = Counter((r["store"] or "?").lower() for r in rows[:100])
        top3 = sum(n for _, n in stores.most_common(3)) / max(min(len(rows), 100), 1)
        own = [r for r in rows if r["is_own"]]
        prices = [p for p in (_price(r["price"]) for r in top) if p]
        past = (today - timedelta(days=days_back)).isoformat()
        risers = []
        for r in rows[:150]:
            h = con.execute("""SELECT rank FROM rank_history WHERE category=? AND product_id=? AND seen_on<=?
                               ORDER BY seen_on DESC LIMIT 1""", (c.key, r["product_id"], past)).fetchone()
            old = h["rank"] if h else None
            if old is None and (r["first_seen"] or "") >= past:
                risers.append((r, None))
            elif old and old - r["rank"] >= 20:
                risers.append((r, old))
        risers.sort(key=lambda x: (x[1] or 10**6) - x[0]["rank"], reverse=True)
        posts = con.execute("""SELECT COUNT(*) FROM posts WHERE category=? AND status IN ('posted','exported')
                               AND posted_at>=?""", (c.key, since)).fetchone()[0]
        weight = c.effective_weight(today) * boost.get(c.key, 1.0)
        best_own = min((r["rank"] for r in own), default=None)
        opportunity = weight * (1.15 - top3) * (0.6 if best_own and best_own <= 120 else 1.0)
        out.append({"cat": c, "weight": round(weight, 2), "boost": boost.get(c.key, 1.0), "in_season": c.in_season(today),
                    "products": len(rows), "stores_top100": len(stores), "top3_share": round(100 * top3),
                    "own_count": len(own), "best_own": best_own, "median_price": round(median(prices), 2) if prices else None,
                    "keywords": lift[:15], "risers": risers[:8], "commission_90d": round(com.get(c.key, 0.0), 2),
                    "posts_90d": posts, "opportunity": round(opportunity, 2), "examples": top[:6]})
    out.sort(key=lambda x: -x["opportunity"])
    return out


def to_html(rows: list[dict], settings: dict) -> str:
    brand = settings["shop"]["brand"]
    summary = "".join(
        f"<tr><td><a href='#{e(r['cat'].key)}'>{e(r['cat'].qs)}</a></td><td>{r['opportunity']}</td><td>{r['weight']}"
        f"{' 🔥' if r['in_season'] else ''}</td><td>{r['boost']}</td><td>{r['products']}</td><td>{r['stores_top100']}</td>"
        f"<td>{r['top3_share']}%</td><td>{r['own_count']} / {r['best_own'] or '—'}</td><td>{r['median_price'] or '—'}</td>"
        f"<td>{r['commission_90d']} / {r['posts_90d']}</td><td>{e(', '.join(k for k, _, _ in r['keywords'][:5]))}</td></tr>"
        for r in rows)
    detail = []
    for r in rows:
        kw = "".join(f"<li><b>{e(t)}</b> — {k} in top 120, lift ×{l}</li>" for t, k, l in r["keywords"])
        rise = "".join(
            f"<li>#{x['rank']} {'(new)' if old is None else f'(was #{old})'} — <a href='{e(x['url'])}'>{e(x['title'][:90])}</a>"
            f" <small>by {e(x['store'] or '?')}</small></li>" for x, old in r["risers"]) or "<li>— (needs 2+ harvests of history)</li>"
        ex = "".join(f"<a href='{e(x['url'])}'><img src='{e(x['image_url'] or '')}' alt='' loading='lazy'></a>" for x in r["examples"])
        detail.append(f"<section id='{e(r['cat'].key)}'><h2>{e(r['cat'].qs)}</h2><div class=ex>{ex}</div>"
                      f"<div class=cols><div><h3>Winning title keywords</h3><ul>{kw}</ul></div>"
                      f"<div><h3>Rising / new in top 150</h3><ul>{rise}</ul></div></div></section>")
    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>選品趨勢報告</title><style>
:root{{--bg:#fbfaf7;--ink:#222;--muted:#666;--line:#e5e1da;--acc:#b8463c}}
@media (prefers-color-scheme:dark){{:root{{--bg:#16171a;--ink:#eee;--muted:#aaa;--line:#33363b;--acc:#e5806f}}}}
body{{margin:0 auto;max-width:1200px;padding:16px;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,sans-serif}}
table{{border-collapse:collapse;width:100%;font-size:.86rem}}td,th{{border-bottom:1px solid var(--line);padding:6px;text-align:left;vertical-align:top}}
.scroll{{overflow-x:auto}}a{{color:var(--acc)}}.ex{{display:flex;gap:6px;overflow-x:auto}}.ex img{{width:110px;height:110px;object-fit:contain;background:#fff;border-radius:6px}}
.cols{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}}section{{border-top:2px solid var(--line);margin-top:24px}}
small{{color:var(--muted)}}</style></head><body>
<h1>選品趨勢報告</h1><p><small>產生時間 {utcnow().isoformat()} · 資料來源：{e(brand)} 熱賣排名（本機資料庫）</small></p>
<p>怎麼看：<b>機會分數</b> = 當季權重 × 銷售加權 × 競爭分散度（前三大店家佔比越低越好）× 你尚未在前 120 名出現。
<b>Winning keywords</b> 是熱賣前 120 名標題中，比 121–900 名明顯更常見的字詞（lift 越高代表越能進前段）。</p>
<div class=scroll><table><thead><tr><th>分類</th><th>機會</th><th>權重</th><th>銷售加權</th><th>商品數</th><th>前100店家數</th>
<th>前三大店家佔比</th><th>自家件數/最佳名次</th><th>前120中位價</th><th>90天佣金/發文</th><th>熱門字</th></tr></thead>
<tbody>{summary}</tbody></table></div>{''.join(detail)}</body></html>"""
