from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from html import escape as e
from pathlib import Path
from xml.sax.saxutils import escape as xe

from .config import Category
from .links import referral_link


def cfg(settings: dict) -> dict:
    return settings["platforms"]["site"].get("newsletter") or {}


def form_html(settings: dict) -> str:
    c = cfg(settings)
    if not c.get("action"):
        return ""
    field = e(c.get("field", "email"))
    hidden = "".join(f'<input type="hidden" name="{e(str(k))}" value="{e(str(v))}">' for k, v in (c.get("hidden") or {}).items())
    return (f'<section style="margin:34px 0 10px;padding:18px;border:1px solid var(--line);border-radius:12px;background:var(--card)">'
            f'<b>{e(c.get("headline", "Get the weekly gift picks"))}</b>'
            f'<p style="margin:4px 0 10px;color:var(--muted);font-size:.9rem">{e(c.get("text", "One short email a week with trending personalizable gifts. Unsubscribe anytime."))}</p>'
            f'<form action="{e(c["action"])}" method="post" target="_blank" style="display:flex;gap:8px;flex-wrap:wrap">{hidden}'
            f'<input type="email" name="{field}" required placeholder="you@example.com" aria-label="Email address" '
            f'style="flex:1;min-width:200px;padding:10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink)">'
            f'<button type="submit" style="padding:10px 18px;border:0;border-radius:8px;background:var(--acc);color:#fff;font-weight:600">'
            f'{e(c.get("button", "Subscribe"))}</button></form></section>')


def weekly_picks(con, settings: dict, cats: list[Category], day: date, n: int = 12) -> list[tuple[dict, Category]]:
    from .perf import category_boost
    boost = category_boost(con, settings, cats)
    week = day.isocalendar()[1]
    ranked = sorted(cats, key=lambda c: -c.effective_weight(day) * boost.get(c.key, 1.0))
    picks, seen = [], set()
    own = con.execute("""SELECT p.*, r.category FROM products p LEFT JOIN rankings r USING(product_id)
                         WHERE p.is_own=1 AND p.alive=1 GROUP BY p.product_id ORDER BY p.first_seen DESC LIMIT 40""").fetchall()
    catmap = {c.key: c for c in cats}
    for i in range(min(3, len(own), n)):
        p = own[(week * 3 + i) % len(own)]
        if p["product_id"] not in seen:
            seen.add(p["product_id"])
            picks.append((dict(p), catmap.get(p["category"]) or ranked[0]))
    offset = week % 5
    for c in ranked:
        if len(picks) >= n:
            break
        rows = con.execute("""SELECT p.* FROM rankings r JOIN products p USING(product_id)
                              WHERE r.category=? AND p.alive=1 AND p.is_own=0 ORDER BY r.rank LIMIT ?""",
                           (c.key, offset + 2)).fetchall()[offset:offset + 2]
        for p in rows:
            if p["product_id"] not in seen and len(picks) < n:
                seen.add(p["product_id"])
                picks.append((dict(p), c))
    return picks


def build_digest(con, settings: dict, cats: list[Category], out: Path) -> dict:
    c = cfg(settings)
    if not c.get("digest", True):
        return {}
    z = settings["shop"]
    site = settings["platforms"]["site"]
    base = site.get("public_url", "").rstrip("/")
    code = c.get("code", "em") if z.get("tracking_code") else None
    today = datetime.now(timezone.utc).date()
    items = []
    for back in (0, 1):
        day = today - timedelta(days=7 * back)
        monday = day - timedelta(days=day.weekday())
        y, w, _ = monday.isocalendar()
        picks = weekly_picks(con, settings, cats, monday)
        if not picks:
            continue
        cards = "".join(
            f'<p><a href="{e(referral_link(p["url"], is_own=bool(p["is_own"]), ambassador_id=z["ambassador_id"], tracking_code=code))}">'
            f'<img src="{e(p["image_url"] or "")}" alt="{e(p["title"])}" width="260" style="max-width:100%"><br>'
            f'<b>{e(p["title"])}</b></a>' + (f' — {e(p["price"])}' if p.get("price") else "") + f'<br><small>{e(cat.qs.title())}</small></p>'
            for p, cat in picks)
        html = (f"<p>{e(c.get('intro', 'This week’s trending personalizable picks:'))}</p>{cards}"
                f"<p><small>{e(settings['copy']['disclosure'])} — links may earn a commission at no extra cost to you.</small></p>")
        when = datetime(monday.year, monday.month, monday.day, 13, 0, tzinfo=timezone.utc)
        items.append(f"<item><title>{xe(c.get('title_prefix', 'Weekly gift picks'))} — {monday.strftime('%b %d, %Y')}</title>"
                     f"<link>{xe(base)}/</link><guid isPermaLink=\"false\">digest-{y}{w:02d}</guid>"
                     f"<pubDate>{format_datetime(when)}</pubDate><description>{xe(html)}</description></item>")
    (out / "digest.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>'
        f"<title>{xe(site['title'])} — weekly picks</title><link>{xe(base)}/</link>"
        f"<description>Weekly personalizable gift picks</description>{''.join(items)}</channel></rss>", encoding="utf-8")
    return {"digest_items": len(items)}
