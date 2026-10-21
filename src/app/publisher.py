from __future__ import annotations

import logging
from datetime import timedelta

from . import copywriter, platforms
from .config import Category
from .db import iso, parse_iso, utcnow
from .planner import SOCIAL, due_posts, reschedule
from .platforms import AuthError, RateLimited

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 3


def _ctx(con, post, catmap, cats):
    product = dict(con.execute("SELECT * FROM products WHERE product_id=?", (post["product_id"],)).fetchone())
    cat = catmap.get(post["category"]) or cats[0]
    return product, cat


def run_posts(con, settings: dict, cats: list[Category], per_run: int = 3) -> dict:
    catmap = {c.key: c for c in cats}
    summary = {}
    for plat in SOCIAL:
        pcfg = settings["platforms"].get(plat, {})
        if not pcfg.get("enabled") or (plat == "pin" and pcfg.get("mode") in ("csv", "rss")):
            continue
        res = {"posted": 0, "failed": 0, "deferred": 0, "error": None}
        due = due_posts(con, plat, per_run)
        if not due:
            summary[plat] = res
            continue
        try:
            client = platforms.get(plat, settings)
        except Exception as e:
            res["error"] = str(e)
            summary[plat] = res
            continue
        for post in due:
            product, cat = _ctx(con, post, catmap, cats)
            copy = copywriter.build(product, cat, settings, plat, post["link"])
            try:
                rid, rurl = client.publish(post, product, cat, copy)
                con.execute("UPDATE posts SET status='posted',posted_at=?,remote_id=?,remote_url=?,error=NULL,"
                            "attempts=attempts+1 WHERE id=?", (iso(utcnow()), rid, rurl, post["id"]))
                res["posted"] += 1
            except RateLimited as e:
                log.warning("%s rate limited: %s", plat, e)
                for p in due:
                    reschedule(con, p["id"], 120)
                res["error"], res["deferred"] = f"rate limited: {e}", len(due) - res["posted"]
                con.commit()
                break
            except AuthError as e:
                res["error"] = f"AUTH: {e}"
                con.execute("UPDATE posts SET error=? WHERE id=?", (str(e)[:500], post["id"]))
                con.commit()
                break
            except Exception as e:
                log.exception("%s publish failed", plat)
                att = post["attempts"] + 1
                status = "failed" if att >= MAX_ATTEMPTS else "scheduled"
                con.execute("UPDATE posts SET attempts=?, status=?, error=? WHERE id=?",
                            (att, status, str(e)[:500], post["id"]))
                if status == "scheduled":
                    reschedule(con, post["id"], 60 * att)
                res["failed"] += 1
                res["error"] = str(e)[:200]
            con.commit()
        summary[plat] = res
    con.execute("UPDATE posts SET status='skipped' WHERE status='scheduled' AND scheduled_at<?",
                (iso(utcnow() - timedelta(days=2)),))
    con.commit()
    return summary


def export_pin_csv(con, settings: dict, cats: list[Category], out_dir) -> list:
    from .boards import board_name, board_specs, cover_suggestions
    from .media import base_url, collage_specs, exported_collages, mark_collages, post_media
    from .platforms.pin import export_boards_csv, export_csv
    catmap = {c.key: c for c in cats}
    rows, ids, titles, used = [], [], set(), set()
    horizon = iso(utcnow() + timedelta(days=8))
    for post in con.execute("""SELECT * FROM posts WHERE platform='pin' AND status='scheduled'
                               AND scheduled_at<=? ORDER BY scheduled_at""", (horizon,)):
        product, cat = _ctx(con, post, catmap, cats)
        copy = copywriter.build(product, cat, settings, "pin", post["link"])
        when = parse_iso(post["scheduled_at"])
        floor = utcnow() + timedelta(hours=1, minutes=45 * len(rows))
        if when < floor:
            when = floor
        title = copy["title"]
        if title.lower() in titles:
            title = (title[:80] + f" – {product.get('store') or product['product_id'][-6:]}")[:100]
        titles.add(title.lower())
        board = board_name(cat, settings)
        used.add(board)
        media = post_media(settings, post, product)
        rows.append({
            "Title": title,
            "Media URL": media["video"] or media["image"],
            "Board": board,
            "Thumbnail": media["image"] if media["video"] else "",
            "Description": copy["description"],
            "Link": post["link"],
            "Publish date": when.strftime("%Y-%m-%dT%H:%M:%S"),
            "Keywords": ", ".join(copy["tags"]),
        })
        ids.append(post["id"])
    done = {r["image_rel"] for r in exported_collages(con)}
    base_day = utcnow().date()
    specs_out = [s for s in collage_specs(con, settings, cats, base_day) if s["image_rel"] not in done]
    for i, s in enumerate(specs_out):
        when = utcnow().replace(hour=20, minute=17, second=0) + timedelta(days=1 + 2 * i)
        title = s["title"] if s["title"].lower() not in titles else s["title"][:90] + " – picks"
        titles.add(title.lower())
        used.add(s["board"])
        rows.append({"Title": title, "Media URL": f"{base_url(settings)}/{s['image_rel']}", "Board": s["board"],
                     "Thumbnail": "", "Description": s["description"], "Link": s["link"],
                     "Publish date": when.strftime("%Y-%m-%dT%H:%M:%S"), "Keywords": ", ".join(s["tags"])})
    if specs_out:
        mark_collages(con, specs_out)
    if not rows:
        return []
    stamp = utcnow().strftime("%Y%m%d")
    files = export_csv(rows, out_dir, stamp, settings["platforms"]["pin"].get("csv_board_column", "Board"))
    specs = board_specs(cats, settings)
    files.insert(0, export_boards_csv(specs, cover_suggestions(con, specs), used, out_dir, stamp))
    con.executemany("UPDATE posts SET status='exported', posted_at=scheduled_at WHERE id=?", [(i,) for i in ids])
    con.commit()
    return files


def collect_metrics(con, settings: dict, days: int = 60) -> dict:
    today = utcnow().date().isoformat()
    since = iso(utcnow() - timedelta(days=days))
    summary = {}
    for plat in SOCIAL:
        pcfg = settings["platforms"].get(plat, {})
        if not pcfg.get("enabled") or (plat == "pin" and pcfg.get("mode") in ("csv", "rss")):
            continue
        posts = con.execute("""SELECT * FROM posts WHERE platform=? AND status='posted' AND remote_id IS NOT NULL
                               AND posted_at>=?""", (plat, since)).fetchall()
        if not posts:
            continue
        try:
            vals = platforms.get(plat, settings).metrics(posts)
        except Exception as e:
            summary[plat] = {"error": str(e)[:200]}
            continue
        for pid, mets in vals.items():
            for k, v in mets.items():
                con.execute("""INSERT INTO metrics(post_id,platform,metric,value,measured_on) VALUES(?,?,?,?,?)
                               ON CONFLICT(post_id,metric,measured_on) DO UPDATE SET value=excluded.value""",
                            (pid, plat, k, float(v or 0), today))
        summary[plat] = {"posts_measured": len(vals)}
    con.commit()
    return summary
