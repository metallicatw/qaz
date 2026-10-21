from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

from . import copywriter
from .boards import board_name
from .config import Category
from .db import iso, utcnow
from .pinimage import TEMPLATES, PALETTES, collage, compose, fetch_image, pick, to_jpeg_bytes

log = logging.getLogger(__name__)


def pcfg(settings: dict) -> dict:
    return settings["platforms"]["pin"]


def base_url(settings: dict) -> str:
    return settings["platforms"]["site"].get("public_url", "").rstrip("/")


def composed(settings: dict) -> bool:
    p = pcfg(settings)
    return p.get("media", "composed") == "composed" and p.get("rss_compose_images", True)


def footer(settings: dict) -> str:
    return f"Personalize it on {settings['shop']['brand']}"


def style(con, settings: dict, post) -> tuple[str, int]:
    names = [t for t in (pcfg(settings).get("templates") or TEMPLATES) if t in TEMPLATES] or list(TEMPLATES)
    n = con.execute("SELECT COUNT(*) FROM posts WHERE platform='pin' AND product_id=? AND id<?",
                    (post["product_id"], post["id"])).fetchone()[0]
    return names[pick(post["product_id"], len(names), n)], pick(post["product_id"] + "p", len(PALETTES), n)


def wants_video(settings: dict, post_id: int) -> bool:
    share = float(pcfg(settings).get("video_share", 0) or 0)
    return share > 0 and pick(f"v{post_id}", 100) < share * 100


def image_rel(post_id: int) -> str:
    return f"pins/{post_id}.jpg"


def video_rel(post_id: int) -> str:
    return f"v/{post_id}.mp4"


def post_media(settings: dict, post, product: dict) -> dict:
    base = base_url(settings)
    if not composed(settings):
        return {"image": product["image_url"], "video": None}
    vid = f"{base}/{video_rel(post['id'])}" if wants_video(settings, post["id"]) else None
    return {"image": f"{base}/{image_rel(post['id'])}", "video": vid}


def render_post(con, settings: dict, post, product: dict, copy: dict, out: Path) -> tuple[Path | None, Path | None]:
    img = out / image_rel(post["id"])
    try:
        if not img.exists():
            template, palette = style(con, settings, post)
            img.parent.mkdir(parents=True, exist_ok=True)
            img.write_bytes(to_jpeg_bytes(compose(fetch_image(product["image_url"]), copy["title"], footer(settings),
                                                  template, product.get("price"), palette)))
    except Exception as ex:
        log.warning("pin image %s failed: %s", post["id"], ex)
        return None, None
    vid = None
    if wants_video(settings, post["id"]):
        from .video import make_video
        try:
            vid = out / video_rel(post["id"])
            if not vid.exists():
                make_video(img, vid, (1000, 1500), int(pcfg(settings).get("video_seconds", 8)))
        except Exception as ex:
            log.warning("pin video %s failed: %s", post["id"], ex)
            vid = None
    return img, vid


def recent_pin_posts(con, days: int):
    since = iso(utcnow() - timedelta(days=days))
    return con.execute("""SELECT * FROM posts WHERE platform='pin' AND status='exported' AND posted_at>=?
                          ORDER BY posted_at""", (since,)).fetchall()


def week_key(day: date) -> str:
    y, w, _ = day.isocalendar()
    return f"{y}{w:02d}"


def collage_specs(con, settings: dict, cats: list[Category], day: date, limit: int | None = None) -> list[dict]:
    n = int(pcfg(settings).get("collage_per_week", 3) or 0) if limit is None else limit
    if n <= 0:
        return []
    from .perf import category_boost
    boost = category_boost(con, settings, cats)
    brand = settings["shop"]["brand"]
    base = base_url(settings)
    week = week_key(day)
    ranked = sorted(cats, key=lambda c: -c.effective_weight(day) * boost.get(c.key, 1.0))
    out = []
    for c in ranked:
        rows = con.execute(
            """SELECT p.*, MIN(r.rank) rk FROM rankings r JOIN products p USING(product_id)
               WHERE r.category=? AND p.alive=1 AND p.image_url IS NOT NULL
               GROUP BY p.product_id ORDER BY p.is_own DESC, rk LIMIT 9""", (c.key,)).fetchall()
        total = con.execute("SELECT COUNT(*) FROM rankings WHERE category=?", (c.key,)).fetchone()[0]
        if len(rows) < 6:
            continue
        title = copywriter.clip(f"{len(rows)} Best {c.qs.title()} to Personalize ({day.year})", 100)
        tags = " ".join("#" + str(t) for t in c.tags[:4])
        desc = copywriter.clip(f"The most popular personalizable {c.qs} on {brand} right now, hand-picked from "
                               f"{total} best sellers. Tap to see them all and add your own names, photos and dates. "
                               f"{settings['copy']['disclosure']} {tags}", 500)
        out.append({"key": c.key, "cat": c, "week": week, "title": title, "description": desc,
                    "board": board_name(c, settings), "link": f"{base}/c/{c.key}.html",
                    "image_rel": f"pins/c-{c.key}-{week}.jpg", "products": [dict(r) for r in rows],
                    "tags": [str(t) for t in c.tags[:5]]})
        if len(out) >= n:
            break
    return out


def collage_spec_for(con, settings: dict, cat: Category, week: str) -> dict | None:
    day = date.fromisocalendar(int(week[:4]), int(week[4:]), 1)
    specs = collage_specs(con, settings, [cat], day, limit=1)
    return specs[0] if specs else None


def exported_collages(con) -> list[dict]:
    import json
    from .db import kv_get
    return json.loads(kv_get(con, "collages_exported", "[]"))


def mark_collages(con, specs: list[dict]):
    import json
    from .db import kv_set
    recs = exported_collages(con) + [{"key": s["key"], "week": s["week"], "image_rel": s["image_rel"]} for s in specs]
    kv_set(con, "collages_exported", json.dumps(recs[-40:]))


def render_collage(spec: dict, settings: dict, out: Path) -> Path | None:
    f = out / spec["image_rel"]
    if f.exists():
        return f
    photos = []
    for p in spec["products"]:
        try:
            photos.append(fetch_image(p["image_url"]))
        except Exception as ex:
            log.warning("collage photo %s failed: %s", p["product_id"], ex)
    if len(photos) < 4:
        return None
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(to_jpeg_bytes(collage(photos, spec["title"], "Tap to see all picks →", pick(spec["key"], len(PALETTES)))))
    return f


def build_pin_media(con, settings: dict, cats: list[Category], out: Path) -> dict:
    p = pcfg(settings)
    if not p.get("enabled") or p.get("mode") not in ("csv", "rss"):
        return {}
    catmap = {c.key: c for c in cats}
    days = max(int(p.get("rss_feed_days", 7)), 10)
    images = videos = 0
    for post in (recent_pin_posts(con, days) if composed(settings) else []):
        product = dict(con.execute("SELECT * FROM products WHERE product_id=?", (post["product_id"],)).fetchone())
        cat = catmap.get(post["category"]) or cats[0]
        copy = copywriter.build(product, cat, settings, "pin", post["link"])
        img, vid = render_post(con, settings, post, product, copy, out)
        images += img is not None
        videos += vid is not None
    today = utcnow().date()
    want = {s["image_rel"]: s for day in (today - timedelta(days=7), today) for s in collage_specs(con, settings, cats, day)}
    for rec in exported_collages(con):
        if rec["image_rel"] not in want and rec["key"] in catmap:
            spec = collage_spec_for(con, settings, catmap[rec["key"]], rec["week"])
            if spec:
                want[spec["image_rel"]] = spec
    collages = sum(render_collage(s, settings, out) is not None for s in want.values())
    return {"pin_images": images, "pin_videos": videos, "collages": collages}
