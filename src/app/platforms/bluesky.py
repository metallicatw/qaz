from __future__ import annotations

from datetime import datetime, timezone

import requests
from PIL import Image

from ..pinimage import fetch_image, to_jpeg_bytes
from .base import check, env

PDS = "https://bsky.social/xrpc"


class Bluesky:
    name = "bluesky"

    def __init__(self, settings: dict):
        self.cfg = settings["platforms"]["bluesky"]
        self.s = requests.Session()
        self.did = None

    def login(self):
        if self.did:
            return
        data = check(self.s.post(f"{PDS}/com.atproto.server.createSession", json={
            "identifier": env("BLUESKY_HANDLE"), "password": env("BLUESKY_APP_PASSWORD")}, timeout=30), "bluesky")
        self.did = data["did"]
        self.s.headers["Authorization"] = f"Bearer {data['accessJwt']}"

    def _thumb(self, url: str):
        try:
            img = fetch_image(url)
        except Exception:
            return None
        img.thumbnail((1000, 1000), Image.LANCZOS)
        data = to_jpeg_bytes(img, max_bytes=950_000)
        r = self.s.post(f"{PDS}/com.atproto.repo.uploadBlob", data=data,
                        headers={"Content-Type": "image/jpeg"}, timeout=60)
        return check(r, "bluesky").get("blob")

    @staticmethod
    def link_facet(text: str, link: str) -> list[dict]:
        b = text.encode("utf-8")
        start = b.find(link.encode("utf-8"))
        if start < 0:
            return []
        return [{"index": {"byteStart": start, "byteEnd": start + len(link.encode())},
                 "features": [{"$type": "app.bsky.richtext.facet#link", "uri": link}]}]

    def publish(self, post, product, category, copy) -> tuple[str, str]:
        self.login()
        text = copy["text"]
        external = {"uri": post["link"], "title": copy["card_title"], "description": copy["card_desc"]}
        thumb = self._thumb(product["image_url"]) if product.get("image_url") else None
        if thumb:
            external["thumb"] = thumb
        record = {
            "$type": "app.bsky.feed.post",
            "text": text,
            "facets": self.link_facet(text, post["link"]),
            "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "langs": ["en"],
            "embed": {"$type": "app.bsky.embed.external", "external": external},
        }
        data = check(self.s.post(f"{PDS}/com.atproto.repo.createRecord", json={
            "repo": self.did, "collection": "app.bsky.feed.post", "record": record}, timeout=60), "bluesky")
        uri = data["uri"]
        rkey = uri.rsplit("/", 1)[-1]
        return uri, f"https://bsky.app/profile/{self.did}/post/{rkey}"

    def metrics(self, posts) -> dict[int, dict]:
        self.login()
        out, by_uri = {}, {p["remote_id"]: p["id"] for p in posts}
        uris = list(by_uri)
        for i in range(0, len(uris), 25):
            r = self.s.get("https://bsky.social/xrpc/app.bsky.feed.getPosts",
                           params=[("uris", u) for u in uris[i:i + 25]], timeout=30)
            if r.status_code != 200:
                continue
            for p in r.json().get("posts", []):
                out[by_uri[p["uri"]]] = {"likes": p.get("likeCount", 0), "reposts": p.get("repostCount", 0),
                                         "replies": p.get("replyCount", 0), "quotes": p.get("quoteCount", 0)}
        return out


