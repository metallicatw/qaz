from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    product_id   TEXT PRIMARY KEY,
    title        TEXT,
    url          TEXT,          -- canonical product URL, no query string
    image_url    TEXT,
    price        TEXT,
    store        TEXT,
    description  TEXT,
    keywords     TEXT,
    is_own       INTEGER DEFAULT 0,
    first_seen   TEXT,
    last_seen    TEXT,
    alive        INTEGER DEFAULT 1
);
-- where a product ranks in a category (one product can rank in several)
CREATE TABLE IF NOT EXISTS rankings (
    category     TEXT,
    product_id   TEXT,
    page         INTEGER,
    rank         INTEGER,        -- 1-based overall rank within category
    seen_at      TEXT,
    PRIMARY KEY (category, product_id)
);
CREATE TABLE IF NOT EXISTS harvest_pages (
    category     TEXT,
    page         INTEGER,
    fetched_at   TEXT,
    n_items      INTEGER,
    status       TEXT,
    PRIMARY KEY (category, page)
);
CREATE TABLE IF NOT EXISTS posts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    platform     TEXT,
    product_id   TEXT,
    category     TEXT,
    link         TEXT,           -- the exact link published (with/without rf)
    is_own       INTEGER,
    scheduled_at TEXT,           -- UTC ISO
    status       TEXT,           -- scheduled | posted | failed | skipped | exported
    posted_at    TEXT,
    remote_id    TEXT,
    remote_url   TEXT,
    error        TEXT,
    attempts     INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_posts_pp ON posts(platform, product_id);
CREATE INDEX IF NOT EXISTS ix_posts_status ON posts(status, scheduled_at);
CREATE TABLE IF NOT EXISTS metrics (
    post_id      INTEGER,
    platform     TEXT,
    metric       TEXT,
    value        REAL,
    measured_on  TEXT,           -- YYYY-MM-DD
    PRIMARY KEY (post_id, metric, measured_on)
);
CREATE TABLE IF NOT EXISTS sales (
    sale_key     TEXT PRIMARY KEY,   -- hash of the source row
    sale_date    TEXT,
    product_id   TEXT,
    title        TEXT,
    amount       REAL,
    commission   REAL,
    kind         TEXT,           -- self | cross | royalty | unknown
    tracking     TEXT,           -- tc code if present
    source_file  TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    command      TEXT,
    started_at   TEXT,
    finished_at  TEXT,
    ok           INTEGER,
    summary      TEXT
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def kv_get(con, k, default=None):
    r = con.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    return r["v"] if r else default


def kv_set(con, k, v):
    con.execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))
