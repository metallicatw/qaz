from __future__ import annotations

import json
import logging
import re
from datetime import date
from pathlib import Path

import yaml

from .config import Category

log = logging.getLogger(__name__)
REQUIRED = ("slug", "title", "category", "intro")
MODEL = "claude-opus-5-5"


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:80]


def load_guides(folder: Path) -> list[dict]:
    from .crypto import decrypt_bytes
    out = []
    for f in sorted(folder.glob("*.yaml.enc")) + sorted(folder.glob("*.yaml")):
        try:
            raw = decrypt_bytes(f.read_bytes()).decode("utf-8") if f.suffix == ".enc" else f.read_text(encoding="utf-8")
            g = yaml.safe_load(raw) or {}
        except Exception as ex:
            log.warning("guide %s unreadable: %s", f.name, ex)
            continue
        missing = [k for k in REQUIRED if not g.get(k)]
        if missing or g.get("draft"):
            log.warning("guide %s skipped (missing %s / draft)", f.name, missing)
            continue
        g["slug"] = slugify(str(g["slug"]))
        out.append(g)
    seen, uniq = set(), []
    for g in out:
        if g["slug"] not in seen:
            seen.add(g["slug"])
            uniq.append(g)
    return uniq


def pick_products(con, category: str, count: int, ids: list[str] | None = None) -> list[dict]:
    if ids:
        rows = []
        for pid in ids:
            r = con.execute("""SELECT p.*, (SELECT MIN(rank) FROM rankings WHERE product_id=p.product_id) rank
                               FROM products p WHERE p.product_id=? AND p.alive=1""", (str(pid),)).fetchone()
            if r:
                rows.append(dict(r))
        return rows
    return [dict(r) for r in con.execute(
        """SELECT p.*, MIN(r.rank) rank FROM rankings r JOIN products p USING(product_id)
           WHERE r.category=? AND p.alive=1 GROUP BY p.product_id ORDER BY p.is_own DESC, rank LIMIT ?""",
        (category, count))]


def guide_products(con, g: dict) -> list[tuple[dict, str]]:
    notes = {str(n.get("product_id")): n.get("note", "") for n in g.get("notes") or [] if isinstance(n, dict)}
    ids = [str(x) for x in g.get("products") or []] or None
    rows = pick_products(con, g["category"], int(g.get("count", 24)), ids)
    return [(r, notes.get(r["product_id"], "")) for r in rows]


def _paras(text) -> list[str]:
    if isinstance(text, list):
        return [str(t).strip() for t in text if str(t).strip()]
    return [p.strip() for p in str(text or "").split("\n\n") if p.strip()]


def render(con, g: dict, settings: dict, link_fn, card_fn) -> tuple[str, str]:
    from html import escape as e
    items = guide_products(con, g)
    intro = "".join(f"<p>{e(p)}</p>" for p in _paras(g["intro"]))
    tips = "".join(f"<li>{e(str(t))}</li>" for t in g.get("tips") or [])
    tips_html = f"<h2>How to choose</h2><ul>{tips}</ul>" if tips else ""
    cards = []
    for p, note in items:
        cards.append(f'<div>{card_fn(p, link_fn(p), "g-" + g["slug"])}'
                     + (f'<p style="font-size:.88rem;margin:6px 2px 0">{e(note)}</p>' if note else "") + "</div>")
    faq = [f for f in g.get("faq") or [] if isinstance(f, dict) and f.get("q") and f.get("a")]
    faq_html = ("<h2>FAQ</h2>" + "".join(f"<details><summary><b>{e(f['q'])}</b></summary><p>{e(f['a'])}</p></details>"
                                         for f in faq)) if faq else ""
    updated = e(str(g.get("updated") or g.get("published") or date.today().isoformat()))
    ld = [{"@context": "https://schema.org", "@type": "ItemList", "name": g["title"],
           "itemListElement": [{"@type": "ListItem", "position": i + 1, "name": p["title"], "url": link_fn(p)}
                               for i, (p, _) in enumerate(items)]}]
    if faq:
        ld.append({"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
            {"@type": "Question", "name": f["q"], "acceptedAnswer": {"@type": "Answer", "text": f["a"]}} for f in faq]})
    body = (f"<article><h2 style='margin-top:6px;font-size:1.5rem'>{e(g['title'])}</h2>"
            f"<p style='color:var(--muted);font-size:.85rem'>Updated {updated}</p>{intro}{tips_html}"
            f"<h2>The picks</h2><div class=grid>{''.join(cards)}</div>{faq_html}</article>"
            + "".join(f'<script type="application/ld+json">{json.dumps(x, ensure_ascii=False)}</script>' for x in ld))
    return body, str(g.get("description") or g["title"])


SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "intro": {"type": "array", "items": {"type": "string"}},
        "tips": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "array", "items": {"type": "object", "properties": {
            "product_id": {"type": "string"}, "note": {"type": "string"}},
            "required": ["product_id", "note"], "additionalProperties": False}},
        "faq": {"type": "array", "items": {"type": "object", "properties": {
            "q": {"type": "string"}, "a": {"type": "string"}}, "required": ["q", "a"], "additionalProperties": False}},
    },
    "required": ["title", "description", "intro", "tips", "notes", "faq"],
    "additionalProperties": False,
}

SYSTEM = """You write copy for a small, independent gift-guide website that curates personalizable products from {brand}.
Write in natural, warm US English for shoppers, not for search engines.
Rules:
- Use only facts present in the product data (titles, prices, descriptions). Never invent materials, sizes, shipping times, discounts, reviews or awards.
- Customization claims stay general: shoppers can add their own names, photos, dates, colors or text.
- The products come from the marketplace's best-seller ranking, so "popular" or "best-selling" is accurate; do not claim "#1" or "the best on the internet".
- Title: under 65 characters, includes the main topic naturally. Description: under 155 characters.
- Intro: 2-3 short paragraphs that help someone decide (occasion, timing, how to pick a style).
- Tips: 4-6 concrete, practical buying tips for this kind of product.
- Notes: one sentence per product (max 160 characters) saying who it suits or what stands out, based on its title and description.
- FAQ: 3-5 questions a real shopper would ask, answered without inventing facts; when unsure, tell them to check the product page.
- Do not mention affiliate links, commissions or this writing process."""


def _ai_copy(products: list[dict], cat: Category, topic: str, brand: str, year: int) -> dict:
    import anthropic
    client = anthropic.Anthropic()
    data = [{"product_id": p["product_id"], "title": p["title"], "price": p.get("price"), "designer": p.get("store"),
             "description": (p.get("description") or "")[:300], "bestseller_rank": p.get("rank")} for p in products]
    prompt = (f"Topic: {topic}\nCategory search term: {cat.qs}\nYear: {year}\n"
              f"Write the guide for these {len(data)} products (keep this order, one note each):\n"
              + json.dumps(data, ensure_ascii=False, indent=1))
    resp = client.beta.messages.create(
        model=MODEL, max_tokens=16000, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        system=SYSTEM.format(brand=brand),
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": prompt}])
    if resp.stop_reason == "refusal":
        raise RuntimeError("model declined the request")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("response was cut off (max_tokens)")
    return json.loads(next(b.text for b in resp.content if b.type == "text"))


def draft(con, settings: dict, cats: list[Category], category: str, count: int, topic: str | None,
          out_dir: Path, use_ai: bool = True) -> tuple[Path, str]:
    catmap = {c.key: c for c in cats}
    if category not in catmap:
        raise ValueError(f"unknown category {category}; choose one of: {', '.join(catmap)}")
    cat = catmap[category]
    products = pick_products(con, category, count)
    if not products:
        raise ValueError(f"no harvested products for {category} yet")
    year = date.today().year
    topic = topic or f"{cat.qs} gift guide {year}"
    brand = settings["shop"]["brand"]
    note = "ai"
    try:
        if not use_ai:
            raise RuntimeError("ai disabled")
        copy = _ai_copy(products, cat, topic, brand, year)
    except Exception as ex:
        note = f"template ({ex})"
        copy = {"title": f"{len(products)} Best {cat.qs.title()} to Personalize in {year}",
                "description": f"Our favorite best-selling personalizable {cat.qs} for {year}.",
                "intro": ["TODO: write 2-3 short paragraphs for shoppers."], "tips": ["TODO"],
                "notes": [{"product_id": p["product_id"], "note": ""} for p in products], "faq": []}
    g = {"slug": slugify(copy["title"]), "title": copy["title"], "description": copy["description"],
         "category": category, "count": len(products), "published": date.today().isoformat(),
         "intro": copy["intro"], "tips": copy["tips"], "products": [p["product_id"] for p in products],
         "notes": copy["notes"], "faq": copy["faq"], "draft": True}
    out_dir.mkdir(parents=True, exist_ok=True)
    f = out_dir / f"{g['slug']}.yaml"
    f.write_text(yaml.safe_dump(g, allow_unicode=True, sort_keys=False, width=110), encoding="utf-8")
    return f, note


def publish(path: Path, dest: Path) -> Path:
    from .crypto import encrypt_bytes
    g = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    g.pop("draft", None)
    missing = [k for k in REQUIRED if not g.get(k)]
    if missing:
        raise ValueError(f"missing fields: {missing}")
    if any("TODO" in str(x) for x in [g.get("intro"), g.get("tips")]):
        raise ValueError("draft still contains TODO text")
    g["updated"] = date.today().isoformat()
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / f"{slugify(str(g['slug']))}.yaml.enc"
    out.write_bytes(encrypt_bytes(yaml.safe_dump(g, allow_unicode=True, sort_keys=False).encode("utf-8")))
    return out
