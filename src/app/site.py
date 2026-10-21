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


def _page(title: str, body: str, desc: str, cats: list[Category], active: str | None, site: dict, depth: int) -> str:
    brand = site["brand"]
    up = "../" * depth
    verify = f'<meta name="p:domain_verify" content="{e(site["pin_verify"])}">' if site.get("pin_verify") else ""
    nav = "".join(f'<a href="{up}c/{c.key}.html" class="{"on" if c.key == active else ""}">{e(c.qs.title())}</a>' for c in cats)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(title)}</title>
<meta name="description" content="{e(desc)}">{verify}<style>{CSS}</style></head><body>
<header><a href="{up}index.html" style="color:inherit;text-decoration:none"><h1>{e(site['title'])}</h1></a>
<p>Hand-picked, best-selling personalizable designs from independent {e(brand)} creators.</p><nav>{nav}</nav></header>
<main>{body}</main>
<footer>Disclosure: links on this site are {e(brand)} affiliate / referral links — if you buy through them I may earn a
commission at no extra cost to you. Product images © their respective {e(brand)} creators.</footer></body></html>"""


def _card(p, link: str, show_rank=True) -> str:
    img = e(p["image_url"] or "")
    rank = f'<span class="rank">#{p["rank"]}</span> ' if show_rank and "rank" in p.keys() else ""
    by = f"<small>by {e(p['store'])}</small>" if p["store"] else ""
    price = f" · <small>{e(p['price'])}</small>" if p["price"] else ""
    return (f'<a class="card" href="{e(link)}" rel="sponsored noopener" target="_blank">'
            f'<img loading="lazy" src="{img}" alt="{e(p["title"])}"><div>{rank}<b>{e(p["title"][:90])}</b>{by}{price}</div></a>')


def build(con, settings: dict, cats: list[Category], stats: dict, out: Path):
    z = settings["shop"]
    site = {**settings["platforms"]["site"], "brand": z["brand"]}
    brand = z["brand"]
    tc = site.get("code") if z.get("tracking_code") else None
    out.mkdir(parents=True, exist_ok=True)
    (out / "c").mkdir(exist_ok=True)
    (out / "report").mkdir(exist_ok=True)
    (out / ".nojekyll").write_text("")

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
            sections.append(_card(p, link(p)))
            if i == len(rows) - 1 or rows[i + 1]["page"] != cur:
                sections.append("</div>")
        body = f"<h2 style='margin-top:6px'>Best-selling {e(c.qs)} on {e(brand)}</h2>" + "".join(sections)
        (out / "c" / f"{c.key}.html").write_text(
            _page(f"Best {c.qs.title()} on {brand} — top picks", body,
                  f"The most popular personalizable {c.qs} on {brand}, updated every two weeks.", cats, c.key, site, 1),
            encoding="utf-8")
        urls.append(f"c/{c.key}.html")
        tiles.append(f'<a class="card" href="c/{c.key}.html"><img loading="lazy" src="{e(rows[0]["image_url"] or "")}" '
                     f'alt="{e(c.qs)}"><div><b>{e(c.qs.title())}</b><small>{len(rows)} picks</small></div></a>')

    own = con.execute("SELECT * FROM products WHERE is_own=1 AND alive=1 ORDER BY first_seen DESC LIMIT 24").fetchall()
    own_html = ("<h2>From my own shops</h2><div class=grid>" + "".join(_card(p, link(p), False) for p in own) + "</div>") if own else ""
    index = own_html + "<h2>Browse categories</h2><div class=grid>" + "".join(tiles) + "</div>"
    (out / "index.html").write_text(_page(site["title"], index, f"Curated best-selling {brand} gifts, invitations and décor.",
                                          cats, None, site, 0), encoding="utf-8")
    public_report = settings.get("privacy", {}).get("public_report", False)
    if public_report:
        (out / "report" / "index.html").write_text(to_html(stats), encoding="utf-8")
    if public_report:
        (out / "report" / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, default=str, indent=1), encoding="utf-8")
    base = site.get("public_url", "").rstrip("/")
    (out / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(f"<url><loc>{base}/{u}</loc></url>" for u in ["index.html", *urls]) + "</urlset>")
    (out / "robots.txt").write_text(f"User-agent: *\nDisallow: /report/\nSitemap: {base}/sitemap.xml\n")
    from .pinrss import build_feeds
    return {"category_pages": len(urls), **build_feeds(con, settings, cats, out)}
