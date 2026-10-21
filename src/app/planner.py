from __future__ import annotations

import random
from datetime import date, datetime, timedelta, timezone

from .config import Category
from .db import iso, parse_iso, utcnow
from .links import referral_link

SOCIAL = ("pin", "tumblr", "bluesky", "threads")


def daily_cap(pcfg: dict, day: date) -> int:
    start = date.fromisoformat(str(pcfg.get("start_date", day.isoformat())))
    if day < start:
        return 0
    week = (day - start).days // 7 + 1
    cap = 0
    for from_week, n in sorted(pcfg.get("ramp", [[1, 5]])):
        if week >= from_week:
            cap = n
    return cap


def make_slots(n: int, windows: list[list[int]], min_gap: int, day: date,
               not_before: datetime | None, rng: random.Random) -> list[datetime]:
    base = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    minutes = [m for a, b in windows for m in range(a * 60, min(b, 24) * 60)]
    if not_before:
        minutes = [m for m in minutes if base + timedelta(minutes=m) > not_before + timedelta(minutes=5)]
    rng.shuffle(minutes)
    chosen: list[int] = []
    for m in minutes:
        if len(chosen) >= n:
            break
        if all(abs(m - c) >= min_gap for c in chosen):
            chosen.append(m)
    return sorted(base + timedelta(minutes=m) for m in chosen)


def _posted_ids(con, platform: str, cooldown_days: int) -> set[str]:
    since = iso(utcnow() - timedelta(days=cooldown_days))
    rows = con.execute(
        """SELECT product_id FROM posts WHERE platform=? AND
           (status IN ('scheduled','exported') OR (status='posted' AND posted_at>=?))""",
        (platform, since))
    return {r["product_id"] for r in rows}


def _ever_posted(con, platform: str) -> set[str]:
    return {r["product_id"] for r in con.execute(
        "SELECT product_id FROM posts WHERE platform=? AND status IN ('posted','scheduled','exported')", (platform,))}


def candidates_by_category(con, cats: list[Category]) -> dict[str, list]:
    out = {}
    for c in cats:
        out[c.key] = con.execute(
            """SELECT p.*, r.rank, r.page FROM rankings r JOIN products p USING(product_id)
               WHERE r.category=? AND p.alive=1 AND p.is_own=0 ORDER BY r.rank""", (c.key,)).fetchall()
    return out


def own_candidates(con) -> list:
    return con.execute(
        """SELECT p.*, MIN(COALESCE(r.rank, 99999)) AS rank, r.category AS category FROM products p
           LEFT JOIN rankings r USING(product_id) WHERE p.is_own=1 AND p.alive=1
           GROUP BY p.product_id ORDER BY rank, p.first_seen""").fetchall()


def plan_day(con, platform: str, settings: dict, cats: list[Category], day: date,
             rng: random.Random | None = None, boost: dict[str, float] | None = None) -> int:
    boost = boost or {}

    def weight(c: Category) -> float:
        return c.effective_weight(day) * boost.get(c.key, 1.0)

    pcfg = settings["platforms"][platform]
    if not pcfg.get("enabled"):
        return 0
    already = con.execute(
        "SELECT COUNT(*) FROM posts WHERE platform=? AND substr(scheduled_at,1,10)=?",
        (platform, day.isoformat())).fetchone()[0]
    cap = daily_cap(pcfg, day)
    n = cap - already
    if n <= 0:
        return 0
    rng = rng or random.Random(f"{platform}-{day}")
    now = utcnow()
    slots = make_slots(n, pcfg["windows_utc"], pcfg.get("min_gap_minutes", 30), day,
                       now if day == now.date() else None, rng)
    if not slots:
        return 0
    n = len(slots)

    z = settings["shop"]
    blocked = _posted_ids(con, platform, settings.get("repost_cooldown_days", 90))
    ever = _ever_posted(con, platform)
    store_count: dict[str, int] = {}
    max_store = settings.get("max_per_store_per_day", 2)
    catmap = {c.key: c for c in cats}
    picks: list[tuple[dict, str]] = []

    def ok(p) -> bool:
        if p["product_id"] in blocked:
            return False
        s = (p["store"] or "?").lower()
        return store_count.get(s, 0) < max_store

    def take(p, cat_key):
        blocked.add(p["product_id"])
        s = (p["store"] or "?").lower()
        store_count[s] = store_count.get(s, 0) + 1
        picks.append((p, cat_key))

    own_quota = round(n * settings.get("own_product_share", 0.25))
    for p in own_candidates(con):
        if len(picks) >= own_quota:
            break
        if p["product_id"] not in blocked:
            blocked.add(p["product_id"])
            picks.append((p, p["category"] or cats[0].key))

    cands = candidates_by_category(con, cats)
    progress = {c.key: sum(1 for p in cands[c.key] if p["product_id"] in ever) for c in cats}
    cursor = {c.key: 0 for c in cats}
    active = [c for c in cats if cands[c.key] and weight(c) > 0]
    while len(picks) < n and active:
        active.sort(key=lambda c: progress[c.key] / weight(c))
        c = active[0]
        lst, i = cands[c.key], cursor[c.key]
        while i < len(lst) and not ok(lst[i]):
            i += 1
        if i >= len(lst):
            active.pop(0)
            continue
        take(lst[i], c.key)
        cursor[c.key] = i + 1
        progress[c.key] += 1

    tc = pcfg.get("code") if z.get("tracking_code") else None
    for when, (p, cat_key) in zip(slots, picks):
        link = referral_link(p["url"], is_own=bool(p["is_own"]), ambassador_id=z["ambassador_id"],
                             tracking_code=tc)
        con.execute(
            """INSERT INTO posts(platform,product_id,category,link,is_own,scheduled_at,status)
               VALUES(?,?,?,?,?,?, 'scheduled')""",
            (platform, p["product_id"], cat_key if cat_key in catmap else cats[0].key,
             link, int(p["is_own"]), iso(when)))
    con.commit()
    return min(len(slots), len(picks))


def plan(con, settings: dict, cats: list[Category], today: date | None = None) -> dict:
    from .perf import category_boost
    today = today or utcnow().date()
    boost = category_boost(con, settings, cats)
    out = {}
    for plat in SOCIAL:
        pcfg = settings["platforms"].get(plat, {})
        if not pcfg.get("enabled"):
            continue
        horizon = 8 if (plat == "pin" and pcfg.get("mode") == "csv") else 1
        out[plat] = sum(plan_day(con, plat, settings, cats, today + timedelta(days=d), boost=boost) for d in range(horizon))
    return out


def coverage(con, cats: list[Category], settings: dict) -> dict:
    z = settings["shop"]
    target = len(cats) * z["pages_per_category"] * z["page_size"]
    harvested = con.execute("SELECT COUNT(*) FROM rankings").fetchone()[0]
    res = {"target_slots": target, "harvested": harvested, "platforms": {}}
    for plat in SOCIAL:
        if not settings["platforms"].get(plat, {}).get("enabled"):
            continue
        done = con.execute(
            """SELECT COUNT(DISTINCT r.category||r.product_id) FROM rankings r JOIN posts p
               ON p.product_id=r.product_id AND p.platform=? AND p.status IN ('posted','exported')""",
            (plat,)).fetchone()[0]
        cap = daily_cap(settings["platforms"][plat], utcnow().date()) or 1
        left = max(harvested - done, 0)
        res["platforms"][plat] = {"done": done, "pct": round(100 * done / max(harvested, 1), 2),
                                  "daily_cap": cap, "eta_days": round(left / cap)}
    return res


def due_posts(con, platform: str, limit: int = 3):
    return con.execute(
        """SELECT * FROM posts WHERE platform=? AND status='scheduled' AND scheduled_at<=?
           ORDER BY scheduled_at LIMIT ?""", (platform, iso(utcnow()), limit)).fetchall()


def reschedule(con, post_id: int, minutes: int):
    r = con.execute("SELECT scheduled_at FROM posts WHERE id=?", (post_id,)).fetchone()
    t = max(parse_iso(r["scheduled_at"]), utcnow()) + timedelta(minutes=minutes)
    con.execute("UPDATE posts SET scheduled_at=? WHERE id=? AND status='scheduled'", (iso(t), post_id))
