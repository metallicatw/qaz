from __future__ import annotations

import html
import json
from datetime import timedelta

from .config import Category
from .db import iso, kv_get, parse_iso, utcnow
from .planner import SOCIAL, coverage

KEY_METRICS = {
    "pin": ["impression", "outbound_click", "save", "pin_click"],
    "tumblr": ["notes"],
    "bluesky": ["likes", "reposts", "replies"],
    "threads": ["views", "likes", "replies", "reposts"],
}


def _latest_metrics(con, since_iso: str) -> dict:
    rows = con.execute("""
        SELECT m.platform, m.metric, SUM(m.value) v FROM metrics m
        JOIN (SELECT post_id, metric, MAX(measured_on) d FROM metrics GROUP BY post_id, metric) last
          ON last.post_id=m.post_id AND last.metric=m.metric AND last.d=m.measured_on
        JOIN posts p ON p.id=m.post_id WHERE p.posted_at>=?
        GROUP BY m.platform, m.metric""", (since_iso,)).fetchall()
    out: dict = {}
    for r in rows:
        out.setdefault(r["platform"], {})[r["metric"]] = r["v"]
    return out


def build_stats(con, settings: dict, cats: list[Category]) -> dict:
    now = utcnow()
    d1, d7, d30 = (iso(now - timedelta(days=n)) for n in (1, 7, 30))
    today = now.date().isoformat()
    st: dict = {"generated_at": iso(now), "platforms": {}, "alerts": []}

    for plat in SOCIAL:
        pcfg = settings["platforms"].get(plat, {})
        if not pcfg.get("enabled"):
            continue
        q = lambda sql, *a: con.execute(sql, (plat, *a)).fetchone()[0]
        done_states = "('posted','exported')"
        p = {
            "mode": pcfg.get("mode", "api"),
            "posted_24h": q(f"SELECT COUNT(*) FROM posts WHERE platform=? AND status IN {done_states} AND posted_at>=?", d1),
            "posted_7d": q(f"SELECT COUNT(*) FROM posts WHERE platform=? AND status IN {done_states} AND posted_at>=?", d7),
            "posted_30d": q(f"SELECT COUNT(*) FROM posts WHERE platform=? AND status IN {done_states} AND posted_at>=?", d30),
            "posted_total": q(f"SELECT COUNT(*) FROM posts WHERE platform=? AND status IN {done_states}"),
            "queued_today": q("SELECT COUNT(*) FROM posts WHERE platform=? AND status='scheduled' AND substr(scheduled_at,1,10)=?", today),
            "failed_7d": q("SELECT COUNT(*) FROM posts WHERE platform=? AND status='failed' AND scheduled_at>=?", d7),
            "own_share_30d": 0.0,
        }
        own = q(f"SELECT COUNT(*) FROM posts WHERE platform=? AND is_own=1 AND status IN {done_states} AND posted_at>=?", d30)
        p["own_share_30d"] = round(100 * own / p["posted_30d"], 1) if p["posted_30d"] else 0.0
        errs = con.execute("""SELECT error, COUNT(*) n FROM posts WHERE platform=? AND error IS NOT NULL
                              AND scheduled_at>=? GROUP BY error ORDER BY n DESC LIMIT 3""", (plat, d1)).fetchall()
        p["errors_24h"] = [f"{r['n']}× {r['error'][:140]}" for r in errs]
        if any("AUTH" in e or "missing secret" in e or "401" in e for e in p["errors_24h"]):
            st["alerts"].append(f"{plat}: 憑證失效或缺少 secret，需要人工處理")
        if p["failed_7d"] >= 5:
            st["alerts"].append(f"{plat}: 7 天內 {p['failed_7d']} 篇發文失敗")
        if p["mode"] not in ("csv", "rss") and p["queued_today"] == 0 and p["posted_24h"] == 0:
            st["alerts"].append(f"{plat}: 過去 24 小時沒有任何發文（檢查排程/候選商品）")
        st["platforms"][plat] = p

    eng = _latest_metrics(con, d30)
    for plat, mets in eng.items():
        if plat in st["platforms"]:
            st["platforms"][plat]["engagement_30d"] = {k: int(v) for k, v in mets.items()}
    pin = eng.get("pin", {})
    if pin.get("impression"):
        st["platforms"]["pin"]["ctr_pct"] = round(100 * pin.get("outbound_click", 0) / pin["impression"], 2)

    st["coverage"] = coverage(con, cats, settings)
    z = settings["shop"]
    total_pages = len(cats) * z["pages_per_category"]
    fetched = con.execute("SELECT COUNT(*) FROM harvest_pages WHERE status IN ('ok','empty')").fetchone()[0]
    st["harvest"] = {
        "pages_fetched": fetched, "pages_total": total_pages,
        "pct": round(100 * fetched / max(total_pages, 1), 1),
        "products": con.execute("SELECT COUNT(*) FROM products").fetchone()[0],
        "own_products": con.execute("SELECT COUNT(*) FROM products WHERE is_own=1").fetchone()[0],
        "errors_24h": con.execute("SELECT COUNT(*) FROM harvest_pages WHERE status='error' AND fetched_at>=?", (d1,)).fetchone()[0],
    }
    if st["harvest"]["errors_24h"] >= 5:
        st["alerts"].append("商品 feed 抓取錯誤偏多（feed 可能改版或被限流）")
    if st["harvest"]["own_products"] == 0:
        st["alerts"].append("尚未抓到任何自家商品 — 檢查 own_stores 設定")

    from .perf import category_boost
    boost = category_boost(con, settings, cats)
    st["categories"] = []
    for c in cats:
        harvested = con.execute("SELECT COUNT(*) FROM rankings WHERE category=?", (c.key,)).fetchone()[0]
        posted = con.execute("""SELECT COUNT(DISTINCT product_id) FROM posts WHERE category=?
                                AND status IN ('posted','exported')""", (c.key,)).fetchone()[0]
        st["categories"].append({"key": c.key, "qs": c.qs, "harvested": harvested, "posted": posted,
                                 "in_season": c.in_season(now.date()),
                                 "boost": boost.get(c.key, 1.0),
                                 "weight": round(c.effective_weight(now.date()) * boost.get(c.key, 1.0), 2)})

    s30 = con.execute("""SELECT kind, COUNT(*) n, SUM(COALESCE(amount,0)) amt, SUM(COALESCE(commission,0)) com
                         FROM sales WHERE sale_date>=? GROUP BY kind""", ((now - timedelta(days=30)).date().isoformat(),)).fetchall()
    st["sales_30d"] = {r["kind"]: {"orders": r["n"], "sales": round(r["amt"], 2), "commission": round(r["com"], 2)} for r in s30}
    st["sales_by_platform"] = [dict(r) for r in con.execute("""
        SELECT p.platform, COUNT(DISTINCT s.sale_key) orders, ROUND(SUM(COALESCE(s.commission,0)),2) commission
        FROM sales s JOIN posts p ON p.product_id=s.product_id AND p.posted_at<=s.sale_date||'T23:59:59Z'
        AND p.status IN ('posted','exported') GROUP BY p.platform""")]
    st["sales_by_tc"] = [dict(r) for r in con.execute("""SELECT COALESCE(tracking,'(none)') tc, COUNT(*) orders,
        ROUND(SUM(COALESCE(commission,0)),2) commission FROM sales GROUP BY tc ORDER BY commission DESC""")]
    st["daily"] = [dict(r) for r in con.execute("""
        SELECT substr(posted_at,1,10) d, COUNT(*) n FROM posts WHERE status IN ('posted','exported')
        AND posted_at>=? GROUP BY d ORDER BY d""", (d30,))]
    st["top_posts"] = [dict(r) for r in con.execute("""
        SELECT p.platform, pr.title, p.remote_url, p.link, MAX(m.value) v, m.metric FROM metrics m
        JOIN posts p ON p.id=m.post_id JOIN products pr ON pr.product_id=p.product_id
        WHERE m.metric IN ('outbound_click','likes','notes','views') GROUP BY p.id
        ORDER BY (m.metric='outbound_click') DESC, v DESC LIMIT 15""")]

    th_set = kv_get(con, "threads_token_set_at")
    if settings["platforms"].get("threads", {}).get("enabled") and th_set:
        age = (now - parse_iso(th_set)).days
        st["threads_token_age_days"] = age
        if age >= 50:
            st["alerts"].append(f"Threads token 已 {age} 天（60 天到期），請執行 threads-refresh")
    runs = con.execute("SELECT command, ok, started_at, summary FROM runs ORDER BY id DESC LIMIT 30").fetchall()
    st["runs"] = [dict(r) for r in runs]
    bad = [r for r in runs[:10] if not r["ok"]]
    if len(bad) >= 3:
        st["alerts"].append(f"最近 10 次執行有 {len(bad)} 次失敗")
    return st


def redact_money(st: dict) -> dict:
    st = json.loads(json.dumps(st, default=str))
    for v in st.get("sales_30d", {}).values():
        v["sales"] = v["commission"] = "—"
    for r in st.get("sales_by_platform", []) + st.get("sales_by_tc", []):
        r["commission"] = "—"
    return st


def to_markdown(st: dict, hide_money: bool = False) -> str:
    if hide_money:
        st = redact_money(st)
    L = [f"# app 每日報告 — {st['generated_at']}", ""]
    if st["alerts"]:
        L += ["## ⚠️ 警示", *[f"- {a}" for a in st["alerts"]], ""]
    L += ["## 平台發文", "| 平台 | 模式 | 24h | 7天 | 30天 | 累計 | 今日待發 | 7天失敗 | 自家商品% | 互動(30天) |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for k, p in st["platforms"].items():
        eng = ", ".join(f"{m}={v}" for m, v in (p.get("engagement_30d") or {}).items()) or "—"
        if p.get("ctr_pct") is not None:
            eng += f", CTR={p['ctr_pct']}%"
        L.append(f"| {k} | {p['mode']} | {p['posted_24h']} | {p['posted_7d']} | {p['posted_30d']} | {p['posted_total']} "
                 f"| {p['queued_today']} | {p['failed_7d']} | {p['own_share_30d']} | {eng} |")
    h, cov = st["harvest"], st["coverage"]
    L += ["", "## 商品庫與進度",
          f"- 分類頁抓取：{h['pages_fetched']}/{h['pages_total']} 頁（{h['pct']}%），商品 {h['products']} 件，其中自家 {h['own_products']} 件",
          *[f"- {k}：已推 {v['done']} / {cov['harvested']}（{v['pct']}%），目前每日 {v['daily_cap']} 篇，預估再 {v['eta_days']} 天推完"
            for k, v in cov["platforms"].items()]]
    boosted = sorted((c for c in st["categories"] if c.get("boost", 1.0) != 1.0), key=lambda c: -c["boost"])
    if boosted:
        L.append("- 依銷售自動加權：" + "、".join(f"{c['key']} ×{c['boost']}" for c in boosted[:8]))
    L += ["", "## 成效（銷售報表匯入）"]
    if st["sales_30d"]:
        for k, v in st["sales_30d"].items():
            L.append(f"- {k}: {v['orders']} 筆 / 銷售 ${v['sales']} / 佣金 ${v['commission']}")
        for r in st["sales_by_platform"]:
            L.append(f"- 歸因 {r['platform']}: {r['orders']} 筆, ${r['commission']}")
    else:
        L.append("- 尚無資料：把銷售 CSV 放到 data/reports/")
    for e in [x for p in st["platforms"].values() for x in p["errors_24h"]][:6]:
        L.append(f"- 錯誤: {e}")
    return "\n".join(L)


def _bars(series: list[dict], key="n", w=560, h=80) -> str:
    if not series:
        return "<p class=muted>尚無資料</p>"
    mx = max(s[key] for s in series) or 1
    bw = min(w / len(series), 22)
    rects = "".join(
        f'<rect x="{i * bw + 1:.1f}" y="{h - s[key] / mx * (h - 14):.1f}" width="{max(bw - 2, 1):.1f}" '
        f'height="{s[key] / mx * (h - 14):.1f}" rx="2"><title>{s["d"]}: {s[key]}</title></rect>'
        for i, s in enumerate(series))
    return f'<svg viewBox="0 0 {w} {h}" class="bars" role="img" aria-label="daily posts">{rects}</svg>'


def to_html(st: dict) -> str:
    e = html.escape
    alerts = "".join(f"<li>{e(a)}</li>" for a in st["alerts"]) or "<li class=ok>一切正常</li>"
    plat_rows = ""
    for k, p in st["platforms"].items():
        eng = " · ".join(f"{e(m)} {v:,}" for m, v in (p.get("engagement_30d") or {}).items()) or "—"
        if p.get("ctr_pct") is not None:
            eng += f" · CTR {p['ctr_pct']}%"
        plat_rows += (f"<tr><td><b>{k}</b><br><small>{p['mode']}</small></td><td>{p['posted_24h']}</td><td>{p['posted_7d']}</td>"
                      f"<td>{p['posted_30d']}</td><td>{p['posted_total']}</td><td>{p['queued_today']}</td>"
                      f"<td>{p['failed_7d']}</td><td>{p['own_share_30d']}%</td><td class=eng>{eng}</td></tr>")
    cov = st["coverage"]
    cov_rows = "".join(
        f"<div class=kpi><span>{k}</span><b>{v['pct']}%</b><div class=bar><i style='width:{min(v['pct'],100)}%'></i></div>"
        f"<small>{v['done']:,} / {cov['harvested']:,} · 每日 {v['daily_cap']} · 約 {v['eta_days']} 天</small></div>"
        for k, v in cov["platforms"].items())
    cat_rows = "".join(
        f"<tr><td>{e(c['qs'])}</td><td>{c['harvested']}</td><td>{c['posted']}</td>"
        f"<td>{'🎃 當季' if c['in_season'] else ('淡季' if c['in_season'] is False else '常年')}</td><td>{c['weight']}</td></tr>"
        for c in sorted(st["categories"], key=lambda c: -c["weight"]))
    sales = "".join(f"<tr><td>{e(k)}</td><td>{v['orders']}</td><td>${v['sales']}</td><td>${v['commission']}</td></tr>"
                    for k, v in st["sales_30d"].items()) or "<tr><td colspan=4 class=muted>尚未匯入銷售報表</td></tr>"
    tc = "".join(f"<tr><td>{e(r['tc'])}</td><td>{r['orders']}</td><td>${r['commission']}</td></tr>" for r in st["sales_by_tc"])
    top = "".join(f"<tr><td>{e(r['platform'])}</td><td><a href='{e(r['remote_url'] or r['link'])}'>{e((r['title'] or '')[:70])}</a></td>"
                  f"<td>{e(r['metric'])}</td><td>{int(r['v'])}</td></tr>" for r in st["top_posts"])
    runs = "".join(f"<tr><td>{e(r['started_at'])}</td><td>{e(r['command'])}</td><td>{'✅' if r['ok'] else '❌'}</td>"
                   f"<td><small>{e((r['summary'] or '')[:160])}</small></td></tr>" for r in st["runs"][:15])
    h = st["harvest"]
    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>app 監控</title>
<meta name="robots" content="noindex"><style>{DASH_CSS}</style></head><body><main>
<h1>app 監控面板</h1><p class=muted>更新時間 {st['generated_at']} (UTC)</p>
<section><h2>警示</h2><ul class=alerts>{alerts}</ul></section>
<section class=kpis>
<div class=kpi><span>分類頁抓取</span><b>{h['pct']}%</b><div class=bar><i style='width:{h['pct']}%'></i></div><small>{h['pages_fetched']}/{h['pages_total']} 頁 · {h['products']:,} 商品 · 自家 {h['own_products']}</small></div>
{cov_rows}</section>
<section><h2>每日發文量（30 天）</h2>{_bars(st['daily'])}</section>
<section><h2>各平台</h2><div class=scroll><table><thead><tr><th>平台</th><th>24h</th><th>7天</th><th>30天</th><th>累計</th><th>今日待發</th><th>7天失敗</th><th>自家%</th><th>互動（近30天發文）</th></tr></thead><tbody>{plat_rows}</tbody></table></div></section>
<section><h2>佣金（近 30 天，來自銷售報表）</h2><div class=scroll><table><thead><tr><th>類型</th><th>筆數</th><th>銷售額</th><th>佣金</th></tr></thead><tbody>{sales}</tbody></table>
<table><thead><tr><th>來源 tc</th><th>筆數</th><th>佣金</th></tr></thead><tbody>{tc}</tbody></table></div></section>
<section><h2>表現最佳貼文</h2><div class=scroll><table><thead><tr><th>平台</th><th>商品</th><th>指標</th><th>值</th></tr></thead><tbody>{top}</tbody></table></div></section>
<section><h2>分類進度</h2><div class=scroll><table><thead><tr><th>分類</th><th>已收錄</th><th>已推薦</th><th>季節</th><th>權重</th></tr></thead><tbody>{cat_rows}</tbody></table></div></section>
<section><h2>執行紀錄</h2><div class=scroll><table><tbody>{runs}</tbody></table></div></section>
<script type="application/json" id="stats">{e(json.dumps(st, ensure_ascii=False, default=str))}</script>
</main></body></html>"""


DASH_CSS = """
:root{--bg:#f7f6f3;--card:#fff;--ink:#1f2328;--muted:#6b7077;--line:#e3e1dc;--acc:#b8463c;--ok:#2f7d4f}
@media (prefers-color-scheme:dark){:root{--bg:#16171a;--card:#1f2125;--ink:#e8e6e3;--muted:#9aa0a6;--line:#33363b;--acc:#e0776b;--ok:#6cc08b}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI","Noto Sans TC",sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px}h1{margin:0 0 4px;font-size:1.6rem}h2{font-size:1.05rem;margin:0 0 10px}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin:14px 0}
.muted{color:var(--muted)}.alerts{margin:0;padding-left:18px}.alerts li{color:var(--acc)}.alerts li.ok{color:var(--ok)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}.kpi span{color:var(--muted);font-size:.85rem}
.kpi b{display:block;font-size:1.6rem;font-variant-numeric:tabular-nums}.kpi small{color:var(--muted)}
.bar{height:6px;background:var(--line);border-radius:3px;margin:6px 0}.bar i{display:block;height:100%;background:var(--acc);border-radius:3px}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;margin-bottom:10px}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:.8rem;color:var(--muted);font-weight:600}td{font-variant-numeric:tabular-nums}td.eng{font-size:.85rem}a{color:var(--acc)}
.bars{width:100%;height:90px}.bars rect{fill:var(--acc)}
"""
