from __future__ import annotations

import html
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from .links import canonical, product_id_from_url

NS = {"media": "http://search.yahoo.com/mrss/"}
UA = "app/1.0 (RSS reader)"
PRICE_RE = re.compile(r"[$€£¥]\s?\d[\d,]*(?:\.\d{2})?")


@dataclass
class FeedItem:
    product_id: str
    title: str
    url: str
    image_url: str | None
    price: str | None
    store: str | None
    description: str
    keywords: str


def _text(el, path, ns=NS):
    x = el.find(path, ns)
    return (x.text or "").strip() if x is not None and x.text else ""


def _attr(el, path, attr, ns=NS):
    x = el.find(path, ns)
    return x.get(attr) if x is not None else None


def hires(url: str | None, max_dim: int = 1000) -> str | None:
    if not url:
        return url
    p = urlsplit(url)
    q = dict(parse_qsl(p.query))
    if "max_dim" in q or "rlvnet" in q:
        q["max_dim"] = str(max_dim)
        return urlunsplit((p.scheme or "https", p.netloc, p.path, urlencode(q), ""))
    return url


def _store_from_author(author: str) -> str | None:
    if not author:
        return None
    m = re.search(r"\(([^)]+)\)", author)
    s = m.group(1) if m else author
    s = re.sub(r"(?i)^designed by\s+", "", s).strip()
    return s or None


def _clean_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def parse_feed(xml_bytes: bytes) -> list[FeedItem]:
    root = ET.fromstring(xml_bytes)
    out: list[FeedItem] = []
    for it in root.iter("item"):
        link = _text(it, "link") or _text(it, "guid")
        pid = product_id_from_url(link) if link else None
        if not pid:
            continue
        desc_html = _text(it, "description")
        price = _text(it, "price") or None
        if not price:
            m = PRICE_RE.search(_clean_html(desc_html))
            price = m.group(0) if m else None
        img = (_attr(it, "media:content", "url") or _attr(it, "media:thumbnail", "url"))
        if not img:
            m = re.search(r'<img[^>]+src="([^"]+)"', desc_html or "")
            img = html.unescape(m.group(1)) if m else None
        out.append(FeedItem(
            product_id=pid,
            title=html.unescape(_text(it, "title") or _text(it, "media:title")),
            url=canonical(link),
            image_url=hires(img),
            price=price,
            store=_store_from_author(_text(it, "author") or _text(it, "{http://purl.org/dc/elements/1.1/}creator", {})),
            description=_clean_html(_text(it, "media:description") or desc_html)[:600],
            keywords=_text(it, "media:keywords"),
        ))
    return out


class FeedClient:
    def __init__(self, feed_base: str, delay: float = 3.0, session: requests.Session | None = None):
        self.base = feed_base.rstrip("/")
        self.delay = delay
        self.s = session or requests.Session()
        self.s.headers["User-Agent"] = UA
        self._last = 0.0

    def _get(self, url: str, params: dict) -> bytes:
        wait = self.delay - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        for attempt in range(4):
            r = self.s.get(url, params=params, timeout=30)
            self._last = time.monotonic()
            if r.status_code in (429, 503):
                time.sleep(30 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.content
        r.raise_for_status()
        return r.content

    def search(self, qs: str, page: int, page_size: int = 60, sort_period: int = 30) -> list[FeedItem]:
        params = {"qs": qs, "st": "popularity", "sp": sort_period, "pg": page,
                  "ps": min(page_size, 100), "isz": "huge"}
        return parse_feed(self._get(f"{self.base}/rss", params))

    def store(self, store: str, page: int, page_size: int = 100) -> list[FeedItem]:
        params = {"st": "popularity", "sp": 0, "pg": page, "ps": min(page_size, 100), "isz": "huge"}
        return parse_feed(self._get(f"{self.base}/{store}/rss", params))
