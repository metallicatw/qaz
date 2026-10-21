from __future__ import annotations

import csv
from pathlib import Path

from . import copywriter
from .config import Category
from .db import utcnow
from .links import referral_link
from .pinimage import PALETTES, TEMPLATES, compose, fetch_image, pick, story, to_jpeg_bytes
from .video import make_video


def make_shorts(con, settings: dict, cats: list[Category], out_dir: Path, limit: int = 10, seconds: int = 10) -> dict:
    from .newsletter import weekly_picks
    z = settings["shop"]
    code = settings.get("shorts", {}).get("code", "sv") if z.get("tracking_code") else None
    brand = z["brand"]
    day = utcnow().date()
    folder = out_dir / day.isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    rows, made, failed = [], 0, 0
    for i, (p, cat) in enumerate(weekly_picks(con, settings, cats, day, limit)):
        link = referral_link(p["url"], is_own=bool(p["is_own"]), ambassador_id=z["ambassador_id"], tracking_code=code)
        copy = copywriter.build(p, cat, settings, "pin", link)
        name = f"{i + 1:02d}-{p['product_id']}"
        try:
            card = compose(fetch_image(p["image_url"]), copy["title"], f"Personalize it on {brand}",
                           TEMPLATES[pick(p["product_id"], len(TEMPLATES))], p.get("price"), pick(p["product_id"], len(PALETTES)))
            img = folder / f"{name}.jpg"
            img.write_bytes(to_jpeg_bytes(story(card, palette=pick(p["product_id"], len(PALETTES)))))
            make_video(img, folder / f"{name}.mp4", (1080, 1920), seconds)
        except Exception as ex:
            failed += 1
            print(f"skip {p['product_id']}: {ex}")
            continue
        tags = " ".join("#" + t for t in copy["tags"])
        caption = f"{copy['title']}\n\n{copywriter.clip(p.get('description') or cat.qs, 180)}\n\nLink in bio ✨ {settings['copy']['disclosure']}\n{tags}"
        (folder / f"{name}.txt").write_text(f"{caption}\n\nLINK: {link}\n", encoding="utf-8")
        rows.append({"file": f"{name}.mp4", "title": copy["title"], "link": link, "caption": caption})
        made += 1
    with open(folder / "index.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=["file", "title", "link", "caption"])
        w.writeheader()
        w.writerows(rows)
    return {"folder": str(folder), "videos": made, "failed": failed}
