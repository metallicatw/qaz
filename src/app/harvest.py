from __future__ import annotations

import logging
from datetime import timedelta

from .config import Category
from .db import iso, parse_iso, utcnow
from .links import is_own_store
from .feed import FeedClient, FeedItem

log = logging.getLogger(__name__)


def pages_due(con, cats: list[Category], settings: dict, today) -> list[tuple[Category, int]]:
    z = settings["shop"]
    refresh = timedelta(days=z.get("refresh_days", 14))
    now = utcnow()
    done = {(r["category"], r["page"]): r for r in con.execute("SELECT * FROM harvest_pages")}
    due = []
    for page in range(1, z["pages_per_category"] + 1):
        for c in sorted(cats, key=lambda c: -c.effective_weight(today)):
            r = done.get((c.key, page))
            if r is None:
                due.append((0, page, c))
            elif r["status"] == "empty":
                continue
            elif now - parse_iso(r["fetched_at"]) > refresh:
                due.append((1, page, c))
    due.sort(key=lambda t: (t[0], t[1]))
    return [(c, p) for _, p, c in due]


def upsert_items(con, items: list[FeedItem], own_stores, own_ids: set[str]):
    now = iso(utcnow())
    for it in items:
        own = int(it.product_id in own_ids or is_own_store(it.store, own_stores))
        con.execute(
            """INSERT INTO products(product_id,title,url,image_url,price,store,description,keywords,is_own,first_seen,last_seen)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(product_id) DO UPDATE SET title=excluded.title,url=excluded.url,
                 image_url=COALESCE(excluded.image_url,products.image_url),price=excluded.price,
                 store=COALESCE(excluded.store,products.store),description=excluded.description,
                 keywords=excluded.keywords,is_own=MAX(products.is_own,excluded.is_own),
                 last_seen=excluded.last_seen,alive=1""",
            (it.product_id, it.title, it.url, it.image_url, it.price, it.store, it.description,
             it.keywords, own, now, now))


def harvest_own_stores(con, client: FeedClient, settings: dict, max_pages: int = 20) -> int:
    z = settings["shop"]
    n = 0
    for store in z.get("own_stores", []):
        for page in range(1, max_pages + 1):
            try:
                items = client.store(store, page)
            except Exception as e:
                log.warning("own store %s p%s failed: %s", store, page, e)
                break
            if not items:
                break
            for it in items:
                it.store = it.store or store
            upsert_items(con, items, z["own_stores"], {i.product_id for i in items})
            n += len(items)
            if len(items) < 100:
                break
    con.commit()
    return n


def run_harvest(con, client: FeedClient, cats: list[Category], settings: dict, today) -> dict:
    z = settings["shop"]
    own_ids = {r["product_id"] for r in con.execute("SELECT product_id FROM products WHERE is_own=1")}
    budget = z.get("harvest_pages_per_run", 40)
    fetched = new_products = errors = 0
    before = con.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    for cat, page in pages_due(con, cats, settings, today)[:budget]:
        try:
            items = client.search(cat.qs, page, z["page_size"], z.get("sort_period", 30))
            status = "ok" if items else "empty"
        except Exception as e:
            log.warning("harvest %s p%s failed: %s", cat.key, page, e)
            items, status = [], "error"
            errors += 1
        if status != "error":
            upsert_items(con, items, z["own_stores"], own_ids)
            base_rank = (page - 1) * z["page_size"]
            for i, it in enumerate(items, 1):
                con.execute(
                    """INSERT INTO rankings(category,product_id,page,rank,seen_at) VALUES(?,?,?,?,?)
                       ON CONFLICT(category,product_id) DO UPDATE SET page=excluded.page,
                       rank=MIN(rankings.rank,excluded.rank),seen_at=excluded.seen_at""",
                    (cat.key, it.product_id, page, base_rank + i, iso(utcnow())))
                if base_rank + i <= int(z.get("history_max_rank", 300)):
                    con.execute("INSERT OR REPLACE INTO rank_history(category,product_id,rank,seen_on) VALUES(?,?,?,?)",
                                (cat.key, it.product_id, base_rank + i, today.isoformat()))
            con.execute(
                """INSERT INTO harvest_pages(category,page,fetched_at,n_items,status) VALUES(?,?,?,?,?)
                   ON CONFLICT(category,page) DO UPDATE SET fetched_at=excluded.fetched_at,
                   n_items=excluded.n_items,status=excluded.status""",
                (cat.key, page, iso(utcnow()), len(items), status))
            fetched += 1
        con.commit()
    con.execute("DELETE FROM rank_history WHERE seen_on<?", ((today - timedelta(days=int(z.get("history_days", 180)))).isoformat(),))
    con.commit()
    new_products = con.execute("SELECT COUNT(*) FROM products").fetchone()[0] - before
    return {"pages_fetched": fetched, "new_products": new_products, "errors": errors}
