from __future__ import annotations

import time

import requests

from .base import PlatformError, check, env

API = "https://graph.threads.net/v1.0"


class Threads:
    name = "threads"

    def __init__(self, settings: dict):
        self.cfg = settings["platforms"]["threads"]
        self.s = requests.Session()

    @property
    def uid(self):
        return env("THREADS_USER_ID")

    @property
    def tok(self):
        return env("THREADS_ACCESS_TOKEN")

    def publish(self, post, product, category, copy) -> tuple[str, str]:
        params = {"media_type": "IMAGE", "image_url": product["image_url"], "text": copy["text"],
                  "access_token": self.tok}
        c = check(self.s.post(f"{API}/{self.uid}/threads", params=params, timeout=60), "threads")
        cid = c.get("id")
        if not cid:
            raise PlatformError(f"threads: no container id {c}")
        for _ in range(10):
            time.sleep(6)
            st = self.s.get(f"{API}/{cid}", params={"fields": "status,error_message",
                                                   "access_token": self.tok}, timeout=30).json()
            if st.get("status") in ("FINISHED", "PUBLISHED"):
                break
            if st.get("status") in ("ERROR", "EXPIRED"):
                raise PlatformError(f"threads container {st}")
        pub = check(self.s.post(f"{API}/{self.uid}/threads_publish",
                                params={"creation_id": cid, "access_token": self.tok}, timeout=60), "threads")
        mid = pub["id"]
        link = self.s.get(f"{API}/{mid}", params={"fields": "permalink", "access_token": self.tok},
                          timeout=30).json().get("permalink", "")
        return mid, link

    def metrics(self, posts) -> dict[int, dict]:
        out = {}
        for p in posts:
            r = self.s.get(f"{API}/{p['remote_id']}/insights", params={
                "metric": "views,likes,replies,reposts,quotes,shares", "access_token": self.tok}, timeout=30)
            if r.status_code != 200:
                continue
            vals = {}
            for m in r.json().get("data", []):
                v = m.get("values", [{}])[0].get("value") if m.get("values") else m.get("total_value", {}).get("value")
                vals[m["name"]] = v or 0
            out[p["id"]] = vals
        return out


def refresh_token(token: str) -> dict:
    r = requests.get("https://graph.threads.net/refresh_access_token",
                     params={"grant_type": "th_refresh_token", "access_token": token}, timeout=30)
    return check(r, "threads")
