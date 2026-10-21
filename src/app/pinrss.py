from __future__ import annotations

import logging
import re
from datetime import timedelta
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape as xe

from . import copywriter
from .boards import board_name, board_specs
from .config import Category
from .db import iso, parse_iso, utcnow

log = logging.getLogger(__name__)


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def export_rss(con, settings: dict) -> int:
    horizon = iso(utcnow() + timedelta(hours=24))
    n = con.execute("""UPDATE posts SET status='exported', posted_at=scheduled_at WHERE platform='pin'
                       AND status='scheduled' AND scheduled_at<=?""", (horizon,)).rowcount
    con.commit()
    return n


def _product_page(product: dict, cat: Category, copy: dict, link: str, img: str, settings: dict,
                  cats: list[Category]) -> str:
    from .site import _page, e
    site = {**settings["platforms"]["site"], "brand": settings["shop"]["brand"]}
    price = f"<p><b>{e(product['price'])}</b></p>" if product.get("price") else ""
    by = f"<p><small>Design by {e(product['store'])}</small></p>" if product.get("store") and not product.get("is_own") else ""
    body = (f'<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:24px;align-items:start">'
            f'<img src="{e(img)}" alt="{e(copy["alt"])}" style="width:100%;border-radius:10px;background:#fff">'
            f'<div><h2 style="margin-top:0">{e(product["title"])}</h2>{price}{by}'
            f'<p>{e(copywriter.clip(product.get("description") or "", 400))}</p>'
            f'<p><a href="{e(link)}" rel="sponsored noopener" style="display:inline-block;background:var(--acc);color:#fff;'
            f'padding:12px 22px;border-radius:999px;text-decoration:none;font-weight:600">Personalize it on {e(settings["shop"]["brand"])} →</a></p>'
            f'<p><small>{e(settings["copy"]["disclosure"])} — more <a href="../c/{cat.key}.html">{e(cat.qs)}</a></small></p>'
            f'</div></div>')
    return _page(f"{product['title']} — {site['title']}", body, copywriter.clip(product.get("description") or product["title"], 160),
                 cats, cat.key, site, 1)


def build_feeds(con, settings: dict, cats: list[Category], out: Path) -> dict:
    from .media import collage_specs, composed, image_rel, render_collage, render_post
    pcfg = settings["platforms"]["pin"]
    if not pcfg.get("enabled") or pcfg.get("mode") != "rss":
        return {}
    site = {**settings["platforms"]["site"], "brand": settings["shop"]["brand"]}
    base = site.get("public_url", "").rstrip("/")
    catmap = {c.key: c for c in cats}
    specs = board_specs(cats, settings)
    since = iso(utcnow() - timedelta(days=int(pcfg.get("rss_feed_days", 7))))
    (out / "p").mkdir(parents=True, exist_ok=True)
    feeds: dict[str, list[str]] = {name: [] for name in specs}

    def item(title, link, guid, when, desc, img_abs, size):
        enclosure = f'<enclosure url="{xe(img_abs)}" type="image/jpeg" length="{size}"/>' if size else ""
        return (f"<item><title>{xe(title)}</title><link>{xe(link)}</link><guid isPermaLink=\"false\">{guid}</guid>"
                f"<pubDate>{format_datetime(when)}</pubDate><description>{xe(desc)}</description>{enclosure}"
                f'<media:content url="{xe(img_abs)}" medium="image" type="image/jpeg"/></item>')

    for post in con.execute("""SELECT * FROM posts WHERE platform='pin' AND status='exported'
                               AND posted_at>=? ORDER BY posted_at DESC""", (since,)):
        product = dict(con.execute("SELECT * FROM products WHERE product_id=?", (post["product_id"],)).fetchone())
        cat = catmap.get(post["category"]) or cats[0]
        copy = copywriter.build(product, cat, settings, "pin", post["link"])
        img, size = product["image_url"], 0
        if composed(settings):
            f, _ = render_post(con, settings, post, product, copy, out)
            if f:
                img, size = f"{base}/{image_rel(post['id'])}", f.stat().st_size
        page = f"p/{product['product_id']}.html"
        (out / page).write_text(_product_page(product, cat, copy, post["link"], img, settings, cats), encoding="utf-8")
        feeds.setdefault(board_name(cat, settings), []).append(
            item(copy["title"], f"{base}/{page}", f"p-{post['id']}", parse_iso(post["posted_at"]), copy["description"], img, size))
    today = utcnow().date()
    for day in (today - timedelta(days=7), today):
        for s in collage_specs(con, settings, cats, day):
            f = render_collage(s, settings, out)
            if f:
                when = utcnow().replace(hour=0, minute=0, second=0) - timedelta(days=(today - day).days)
                feeds.setdefault(s["board"], []).append(item(s["title"], s["link"], f"c-{s['key']}-{s['week']}", when,
                                                             s["description"], f"{base}/{s['image_rel']}", f.stat().st_size))
    d = out / "feeds" / "pin"
    d.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, items in sorted(feeds.items()):
        url = f"{base}/feeds/pin/{slug(name)}.xml"
        desc = specs[name]["description"] if name in specs else name
        (d / f"{slug(name)}.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">'
            f"<channel><title>{xe(name)}</title><link>{xe(base)}/</link><description>{xe(desc)}</description>"
            f"<lastBuildDate>{format_datetime(utcnow())}</lastBuildDate>{''.join(items)}</channel></rss>",
            encoding="utf-8")
        rows.append((name, url, len(items)))
    (d / "index.txt").write_text("".join(f"{n}\t{u}\t{k} items\n" for n, u, k in rows), encoding="utf-8")
    return {"feeds": len(rows), "items": sum(k for _, _, k in rows)}
