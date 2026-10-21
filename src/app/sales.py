from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import datetime
from pathlib import Path

PID = re.compile(r"(?<!\d)(\d{18})(?!\d)")
MONEY = re.compile(r"-?\$?\s?(-?\d[\d,]*\.?\d*)")

DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%b %d, %Y", "%d %b %Y", "%Y/%m/%d", "%m/%d/%Y %I:%M %p")


def _col(headers, *needles):
    for n in needles:
        for h in headers:
            if n in h.lower():
                return h
    return None


def _money(v) -> float | None:
    if v is None:
        return None
    m = MONEY.search(str(v).replace(",", ""))
    try:
        return float(m.group(1)) if m else None
    except ValueError:
        return None


def _date(v) -> str | None:
    v = (v or "").strip()
    for f in DATE_FORMATS:
        try:
            return datetime.strptime(v, f).date().isoformat()
        except ValueError:
            continue
    m = re.search(r"\d{4}-\d{2}-\d{2}", v)
    return m.group(0) if m else None


def _kind(v: str) -> str:
    v = (v or "").lower()
    if "self" in v:
        return "self"
    if "cross" in v or "referral" in v:
        return "cross"
    if "royalt" in v:
        return "royalty"
    return "unknown"


def import_dir(con, folder: Path) -> dict:
    added = files = 0
    paths = sorted(folder.glob("*.csv")) + sorted(folder.glob("*.csv.enc"))
    for f in paths:
        files += 1
        if f.suffix == ".enc":
            from .crypto import decrypt_bytes
            text = decrypt_bytes(f.read_bytes()).decode("utf-8-sig")
        else:
            text = f.read_text(encoding="utf-8-sig")
        with io.StringIO(text, newline="") as fh:
            rd = csv.DictReader(fh)
            h = rd.fieldnames or []
            c_date = _col(h, "date")
            c_title = _col(h, "title", "product name", "item", "description", "product")
            c_amt = _col(h, "sale", "amount", "price", "total")
            c_com = _col(h, "commission", "earning", "referral fee", "payout")
            c_type = _col(h, "type", "program")
            c_tc = _col(h, "tracking", "source")
            for row in rd:
                raw = "|".join(f"{k}={v}" for k, v in sorted(row.items()) if k)
                key = hashlib.sha1(raw.encode()).hexdigest()
                pid = next((m.group(1) for v in row.values() if v for m in [PID.search(str(v))] if m), None)
                cur = con.execute(
                    """INSERT OR IGNORE INTO sales(sale_key,sale_date,product_id,title,amount,commission,kind,tracking,source_file)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (key, _date(row.get(c_date)) if c_date else None, pid,
                     row.get(c_title) if c_title else None,
                     _money(row.get(c_amt)) if c_amt else None,
                     _money(row.get(c_com)) if c_com else None,
                     _kind(row.get(c_type)) if c_type else "unknown",
                     row.get(c_tc) if c_tc else None, f.name))
                added += cur.rowcount
    con.commit()
    return {"files": files, "new_rows": added}
