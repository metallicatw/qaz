from __future__ import annotations

from requests_oauthlib import OAuth1Session

from .base import PlatformError, check, env

API = "https://api.tumblr.com/v2"


class Tumblr:
    name = "tumblr"

    def __init__(self, settings: dict):
        self.cfg = settings["platforms"]["tumblr"]
        self.brand = settings["shop"]["brand"]
        self.blog = self.cfg["blog"]
        self._s = None

    @property
    def s(self) -> OAuth1Session:
        if self._s is None:
            self._s = OAuth1Session(env("TUMBLR_CONSUMER_KEY"), env("TUMBLR_CONSUMER_SECRET"),
                                    env("TUMBLR_TOKEN"), env("TUMBLR_TOKEN_SECRET"))
        return self._s

    def publish(self, post, product, category, copy) -> tuple[str, str]:
        link = post["link"]
        body_txt = copy["body"]
        cta = f"Shop this design on {self.brand} →"
        content = [
            {"type": "image", "media": [{"url": product["image_url"], "type": "image/jpeg"}],
             "alt_text": copy["alt"]},
            {"type": "text", "subtype": "heading2", "text": copy["heading"]},
            {"type": "text", "text": body_txt},
            {"type": "text", "text": cta,
             "formatting": [{"start": 0, "end": len(cta), "type": "link", "url": link}]},
            {"type": "link", "url": link, "title": copy["heading"],
             "description": body_txt[:200], "site_name": self.brand},
            {"type": "text", "subtype": "quote", "text": copy["disclosure"]},
        ]
        payload = {"content": content, "tags": ",".join(copy["tags"]), "state": "published"}
        try:
            data = check(self.s.post(f"{API}/blog/{self.blog}/posts", json=payload, timeout=60), "tumblr")
        except PlatformError as e:
            if " 400" not in str(e):
                raise
            payload["content"] = content[1:]
            data = check(self.s.post(f"{API}/blog/{self.blog}/posts", json=payload, timeout=60), "tumblr")
        pid = str(data.get("response", {}).get("id_string") or data.get("response", {}).get("id"))
        return pid, f"https://{self.blog}.tumblr.com/post/{pid}"

    def metrics(self, posts) -> dict[int, dict]:
        out = {}
        for p in posts:
            r = self.s.get(f"{API}/blog/{self.blog}/posts", params={"id": p["remote_id"]}, timeout=30)
            if r.status_code != 200:
                continue
            items = r.json().get("response", {}).get("posts", [])
            if items:
                out[p["id"]] = {"notes": items[0].get("note_count", 0)}
        return out
