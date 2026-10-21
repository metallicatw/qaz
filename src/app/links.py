from __future__ import annotations

import re
from urllib.parse import urlencode, urlsplit, urlunsplit

PRODUCT_ID_RE = re.compile(r"-(\d{15,20})(?:[/?#]|$)")


def canonical(url: str) -> str:
    p = urlsplit(url.strip())
    netloc = p.netloc
    if netloc.count(".") == 1:
        netloc = "www." + netloc
    return urlunsplit(("https", netloc, p.path, "", ""))


def product_id_from_url(url: str) -> str | None:
    m = PRODUCT_ID_RE.search(urlsplit(url).path + "?")
    return m.group(1) if m else None


def referral_link(url: str, *, is_own: bool, ambassador_id: str,
                  tracking_code: str | None = None) -> str:
    base = canonical(url)
    if is_own:
        return base
    params = {"rf": ambassador_id}
    if tracking_code:
        params["tc"] = tracking_code
    return f"{base}?{urlencode(params)}"


def is_own_store(store: str | None, own_stores: list[str]) -> bool:
    if not store:
        return False
    s = store.strip().lower()
    return any(s == o.strip().lower() for o in own_stores)
