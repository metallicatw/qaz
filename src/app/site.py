from __future__ import annotations

import html
import json
from pathlib import Path

from .config import Category
from .links import referral_link
from .report import to_html

e = html.escape

CSS = """
:root{--bg:#faf8f4;--card:#fff;--ink:#23232a;--muted:#6d6f75;--line:#e7e3dc;--acc:#b8463c}
@media (prefers-color-scheme:dark){:root{--bg:#141518;--card:#1e2024;--ink:#ebe8e3;--muted:#9da1a7;--line:#303338;--acc:#e5806f}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
header,main,footer{max-width:1180px;margin:0 auto;padding:16px}header h1{margin:8px 0 0;font-size:1.7rem}header p{color:var(--muted);margin:4px 0}
nav{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}nav a{border:1px solid var(--line);border-radius:999px;padding:3px 11px;color:var(--ink);text-decoration:none;font-size:.88rem;background:var(--card)}
nav a.on{background:var(--acc);border-color:var(--acc);color:#fff}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden;display:flex;flex-direction:column;text-decoration:none;color:inherit}
.card img{width:100%;aspect-ratio:1;object-fit:contain;background:#fff}.card div{padding:8px 10px}.card b{font-size:.9rem;font-weight:600;display:block}
.card small{color:var(--muted)}.rank{color:var(--acc);font-weight:700;font-size:.8rem}
h2{font-size:1.1rem;margin:26px 0 10px}footer{color:var(--muted);font-size:.85rem;border-top:1px solid var(--line);margin-top:30px}
"""


def head_tags(site: dict) -> str:
    metas = dict(site.get("verify") or {})
    if site.get("pin_verify"):
        metas.setdefault("p:domain_verify", site["pin_verify"])
    out = "".join(f'<meta name="{e(str(k))}" content="{e(str(v))}">' for k, v in metas.items() if v)
    a = site.get("analytics") or {}
    if a.get("goatcounter"):
        out += (f'<script data-goatcounter="https://{e(a["goatcounter"])}.goatcounter.com/count" async '
                f'src="https://gc.zgo.at/count.js"></script>')
    if a.get("cloudflare"):
        out += (f'<script defer src="https://static.cloudflareinsights.com/beacon.min.js" '
                f"data-cf-beacon='{{\"token\": \"{e(a['cloudflare'])}\"}}'></script>")
    return out


def _page(title: str, body: str, desc: str, cats: list[Category], active: str | None, site: dict, depth: int,
          canonical: str | None = None) -> str:
    brand = site["brand"]
    up = "../" * depth
    base = site.get("public_url", "").rstrip("/")
    canon = f'<link rel="canonical" href="{e(base)}/{e(canonical)}">' if canonical and base else ""
    guides = f'<a href="{up}g/index.html" class="{"on" if active == "guides" else ""}"><b>Gift guides</b></a>' if site.get("has_guides") else ""
    nav = guides + "".join(f'<a href="{up}c/{c.key}.html" class="{"on" if c.key == active else ""}">{e(c.qs.title())}</a>' for c in cats)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(title)}</title>
<meta name="description" content="{e(desc)}">{canon}{head_tags(site)}<style>{CSS}</style></head><body>
<header><a href="{up}index.html" style="color:inherit;text-decoration:none"><h1>{e(site['title'])}</h1></a>
<p>Hand-picked, best-selling personalizable designs from independent {e(brand)} creators.</p><nav>{nav}</nav></header>
<main>{body}{site.get("newsletter_html", "")}</main>
<footer>Disclosure: links on this site are {e(brand)} affiliate / referral links — if you buy through them I may earn a
commission at no extra cost to you. Product images © their respective {e(brand)} creators.</footer></body></html>"""


def _card(p, link: str, show_rank=True, where: str = "site") -> str:
    img = e(p["image_url"] or "")
    rank = f'<span class="rank">#{p["rank"]}</span> ' if show_rank and "rank" in p.keys() else ""
    by = f"<small>by {e(p['store'])}</small>" if p["store"] else ""
    price = f" · <small>{e(p['price'])}</small>" if p["price"] else ""
    return (f'<a class="card" href="{e(link)}" rel="sponsored noopener" target="_blank" data-goatcounter-click="out-{e(where)}">'
            f'<img loading="lazy" src="{img}" alt="{e(p["title"])}"><div>{rank}<b>{e(p["title"][:90])}</b>{by}{price}</div></a>')


def build(con, settings: dict, cats: list[Category], stats: dict, out: Path):
    from datetime import date

    from . import guides as gd
    from . import newsletter
    from .config import DATA_DIR
    z = settings["shop"]
    brand = z["brand"]
    site = {**settings["platforms"]["site"], "brand": brand}
    tc = site.get("code") if z.get("tracking_code") else None
    out.mkdir(parents=True, exist_ok=True)
    (out / "c").mkdir(exist_ok=True)
    (out / "report").mkdir(exist_ok=True)
    (out / ".nojekyll").write_text("")
    guides = gd.load_guides(DATA_DIR / "guides")
    site["has_guides"] = bool(guides)
    site["newsletter_html"] = newsletter.form_html(settings)

    def link(p):
        return referral_link(p["url"], is_own=bool(p["is_own"]), ambassador_id=z["ambassador_id"], tracking_code=tc)

    tiles, urls = [], []
    for c in cats:
        rows = con.execute("""SELECT p.*, r.rank, r.page FROM rankings r JOIN products p USING(product_id)
                              WHERE r.category=? AND p.alive=1 ORDER BY r.rank""", (c.key,)).fetchall()
        if not rows:
            continue
        sections, cur = [], None
        for i, p in enumerate(rows):
            if p["page"] != cur:
                cur = p["page"]
                a, b = (cur - 1) * z["page_size"] + 1, cur * z["page_size"]
                sections.append(f"<h2>Top {a}–{b}</h2><div class=grid>")
            sections.append(_card(p, link(p), where=c.key))
            if i == len(rows) - 1 or rows[i + 1]["page"] != cur:
                sections.append("</div>")
        related = [g for g in guides if g["category"] == c.key]
        rel_html = ("<p>Gift guide: " + " · ".join(f'<a href="../g/{g["slug"]}.html">{e(g["title"])}</a>' for g in related)
                    + "</p>") if related else ""
        body = f"<h2 style='margin-top:6px'>Best-selling {e(c.qs)} on {e(brand)}</h2>{rel_html}" + "".join(sections)
        (out / "c" / f"{c.key}.html").write_text(
            _page(f"Best {c.qs.title()} on {brand} — top picks", body,
                  f"The most popular personalizable {c.qs} on {brand}, updated every two weeks.", cats, c.key, site, 1,
                  f"c/{c.key}.html"),
            encoding="utf-8")
        urls.append(f"c/{c.key}.html")
        tiles.append(f'<a class="card" href="c/{c.key}.html"><img loading="lazy" src="{e(rows[0]["image_url"] or "")}" '
                     f'alt="{e(c.qs)}"><div><b>{e(c.qs.title())}</b><small>{len(rows)} picks</small></div></a>')

    guide_tiles = []
    if guides:
        (out / "g").mkdir(exist_ok=True)
        for g in guides:
            body, desc = gd.render(con, g, settings, link, _card)
            (out / "g" / f"{g['slug']}.html").write_text(
                _page(g["title"], body, desc, cats, g["category"], site, 1, f"g/{g['slug']}.html"), encoding="utf-8")
            urls.append(f"g/{g['slug']}.html")
            first = gd.guide_products(con, g)[:1]
            img = e(first[0][0]["image_url"] or "") if first else ""
            guide_tiles.append(f'<a class="card" href="g/{g["slug"]}.html"><img loading="lazy" src="{img}" alt="{e(g["title"])}">'
                               f'<div><b>{e(g["title"])}</b><small>Gift guide</small></div></a>')
        listing = "<h2 style='margin-top:6px'>Gift guides</h2><div class=grid>" + "".join(
            t.replace('href="g/', 'href="') for t in guide_tiles) + "</div>"
        (out / "g" / "index.html").write_text(_page(f"Gift guides — {site['title']}", listing,
                                                    "Curated, seasonal gift guides.", cats, "guides", site, 1, "g/index.html"),
                                              encoding="utf-8")
        urls.append("g/index.html")

    own = con.execute("SELECT * FROM products WHERE is_own=1 AND alive=1 ORDER BY first_seen DESC LIMIT 24").fetchall()
    own_html = ("<h2>From my own shops</h2><div class=grid>" + "".join(_card(p, link(p), False, "own") for p in own) + "</div>") if own else ""
    guide_html = ("<h2>Gift guides</h2><div class=grid>" + "".join(guide_tiles[:8]) + "</div>") if guide_tiles else ""
    index = guide_html + own_html + "<h2>Browse categories</h2><div class=grid>" + "".join(tiles) + "</div>"
    (out / "index.html").write_text(_page(site["title"], index, f"Curated best-selling {brand} gifts, invitations and décor.",
                                          cats, None, site, 0, ""), encoding="utf-8")
    public_report = settings.get("privacy", {}).get("public_report", False)
    if public_report:
        (out / "report" / "index.html").write_text(to_html(stats), encoding="utf-8")
        (out / "report" / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, default=str, indent=1), encoding="utf-8")
    from .media import build_pin_media
    from .pinrss import build_feeds
    res = {"category_pages": len([u for u in urls if u.startswith("c/")]), "guides": len(guides)}
    res.update(build_pin_media(con, settings, cats, out))
    res.update(build_feeds(con, settings, cats, out))
    res.update(newsletter.build_digest(con, settings, cats, out))
    base = site.get("public_url", "").rstrip("/")
    today = date.today().isoformat()
    pages = ["", *urls] + sorted(f"p/{f.name}" for f in (out / "p").glob("*.html")) if (out / "p").exists() else ["", *urls]
    (out / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(f"<url><loc>{base}/{u}</loc><lastmod>{today}</lastmod></url>" for u in pages) + "</urlset>")
    (out / "robots.txt").write_text(f"User-agent: *\nDisallow: /report/\nSitemap: {base}/sitemap.xml\n")
    return res
