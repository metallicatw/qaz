from __future__ import annotations

import hashlib
import re

from .config import Category

STOP = {"the", "and", "for", "with", "your", "you", "a", "an", "of", "to", "in", "on", "custom"}


def _pick(options: list[str], seed: str) -> str:
    h = int(hashlib.md5(seed.encode()).hexdigest(), 16)
    return options[h % len(options)]


def _hashtags(cat: Category, product: dict, n: int, brand: str = "") -> list[str]:
    tags = [str(t) for t in cat.tags]
    for kw in re.split(r"[,\s]+", product.get("keywords") or ""):
        kw = re.sub(r"[^a-z0-9]", "", kw.lower())
        if len(kw) > 3 and kw not in STOP and kw != brand.lower() and kw not in tags:
            tags.append(kw)
    return tags[:n]


def clip(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[: n - 1].rsplit(" ", 1)[0] + "…"


def build(product: dict, cat: Category, settings: dict, platform: str, link: str) -> dict:
    c = settings["copy"]
    brand = settings["shop"]["brand"]
    title = clip(product["title"], 100)
    cta = _pick(c["cta"], product["product_id"] + platform)
    tags = _hashtags(cat, product, c.get("hashtags_max", 5), brand)
    hashtags = " ".join("#" + t for t in tags)
    price = f" — from {product['price']}" if product.get("price") else ""
    store = product.get("store")
    by = "" if product.get("is_own") else (f" Design by {store}." if store else "")
    desc_src = product.get("description") or ""
    disc = c["disclosure"]

    out = {"title": title, "tags": tags, "alt": clip(f"{title}. {cat.qs} on {brand}", 480)}
    if platform == "pin":
        body = f"{title}{price}. {clip(desc_src, 220)} {cta} — fully personalizable.{by} {disc}"
        out["description"] = clip(f"{body} {hashtags}", 500)
    elif platform == "tumblr":
        out["heading"] = title
        out["body"] = clip(f"{clip(desc_src, 260)} {cta}{price}.{by}", 480)
        out["disclosure"] = disc
    elif platform == "bluesky":
        head = clip(f"{title}{price}", 150)
        out["text"] = f"{head}\n{cta} ✨ {disc}\n{link}"
        if len(out["text"]) > 295:
            out["text"] = f"{clip(title, 110)}\n{disc}\n{link}"
        out["card_title"] = title
        out["card_desc"] = clip(desc_src or cat.qs, 250)
    elif platform == "threads":
        text = f"{title}{price}\n\n{cta}.{by}\n{link}\n\n{disc} {hashtags.split(' ')[0] if tags else ''}"
        out["text"] = clip(text, 500)
    else:
        out["description"] = clip(desc_src, 200)
    return out
