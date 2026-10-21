from __future__ import annotations

from datetime import timedelta

from .config import Category
from .db import iso, utcnow


def category_commission(con, cats: list[Category], days: int) -> dict[str, float]:
    since = (utcnow() - timedelta(days=days)).date().isoformat()
    keys = {c.key for c in cats}
    out: dict[str, float] = {}
    for r in con.execute("""SELECT s.product_id, SUM(COALESCE(s.commission,0)) com FROM sales s
                            WHERE s.sale_date>=? AND s.product_id IS NOT NULL GROUP BY s.product_id""", (since,)):
        row = con.execute("""SELECT category FROM posts WHERE product_id=? AND status IN ('posted','exported')
                             ORDER BY posted_at DESC LIMIT 1""", (r["product_id"],)).fetchone()
        if not row or row["category"] not in keys:
            row = con.execute("SELECT category FROM rankings WHERE product_id=? ORDER BY rank LIMIT 1",
                              (r["product_id"],)).fetchone()
        if row and row["category"] in keys:
            out[row["category"]] = out.get(row["category"], 0.0) + float(r["com"] or 0)
    return out


def category_boost(con, settings: dict, cats: list[Category]) -> dict[str, float]:
    cfg = settings.get("auto_weight") or {}
    if not cfg.get("enabled", True):
        return {}
    days = int(cfg.get("days", 60))
    lo, hi, prior = float(cfg.get("min", 0.5)), float(cfg.get("max", 3.0)), float(cfg.get("prior_posts", 20))
    com = category_commission(con, cats, days)
    total_com = sum(com.values())
    if total_com <= 0:
        return {}
    since = iso(utcnow() - timedelta(days=days))
    posts = {r["category"]: r["n"] for r in con.execute(
        """SELECT category, COUNT(*) n FROM posts WHERE status IN ('posted','exported') AND posted_at>=?
           GROUP BY category""", (since,))}
    total_posts = max(sum(posts.get(c.key, 0) for c in cats), 1)
    base = total_com / total_posts
    out = {}
    for c in cats:
        rate = (com.get(c.key, 0.0) + prior * base) / (posts.get(c.key, 0) + prior)
        out[c.key] = round(min(hi, max(lo, rate / base)), 2)
    return out
