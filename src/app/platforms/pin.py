from __future__ import annotations

import base64
import csv
from datetime import date, timedelta
from pathlib import Path

import requests

from ..pinimage import PALETTES, TEMPLATES, make_pin, pick
from .base import AuthError, check, env


class Pin:
    name = "pin"

    def __init__(self, settings: dict):
        self.settings = settings
        self.cfg = settings["platforms"]["pin"]
        self.api = self.cfg["api"]
        self.s = requests.Session()
        self._token = None
        self._boards: dict[str, str] | None = None

    def token(self) -> str:
        if self._token:
            return self._token
        app_id, secret = env("PIN_APP_ID"), env("PIN_APP_SECRET")
        r = requests.post(f"{self.api}/oauth/token",
                          auth=(app_id, secret),
                          data={"grant_type": "refresh_token", "refresh_token": env("PIN_REFRESH_TOKEN")},
                          timeout=30)
        data = check(r, "pin")
        if "access_token" not in data:
            raise AuthError(f"pin token refresh failed: {data}")
        self._token = data["access_token"]
        self.s.headers["Authorization"] = f"Bearer {self._token}"
        self.refresh_token_expires_in = data.get("refresh_token_expires_in")
        return self._token

    def boards(self) -> dict[str, str]:
        if self._boards is None:
            self.token()
            self._boards, bookmark = {}, None
            while True:
                params = {"page_size": 100, **({"bookmark": bookmark} if bookmark else {})}
                data = check(self.s.get(f"{self.api}/boards", params=params, timeout=30), "pin")
                for b in data.get("items", []):
                    self._boards[b["name"].strip().lower()] = b["id"]
                bookmark = data.get("bookmark")
                if not bookmark:
                    break
        return self._boards

    def board_id(self, name: str, description: str = "") -> str:
        key = name.strip().lower()
        if key not in self.boards():
            data = check(self.s.post(f"{self.api}/boards", json={
                "name": name[:50], "description": (description or f"Hand-picked {name}.")[:500],
                "privacy": "PUBLIC"}, timeout=30), "pin")
            self._boards[key] = data["id"]
        return self._boards[key]

    def publish(self, post, product, category, copy) -> tuple[str, str]:
        self.token()
        from ..boards import board_name, board_specs
        name = board_name(category, self.settings)
        spec = board_specs([category], self.settings)[name]
        board = self.board_id(name, spec["description"])
        names = [t for t in (self.cfg.get("templates") or TEMPLATES) if t in TEMPLATES] or list(TEMPLATES)
        img = make_pin(product["image_url"], copy["title"], f"Personalize it on {self.settings['shop']['brand']}",
                       names[pick(product["product_id"], len(names), post["id"])], product.get("price"),
                       pick(product["product_id"] + "p", len(PALETTES), post["id"]))
        body = {
            "board_id": board,
            "title": copy["title"],
            "description": copy["description"],
            "link": post["link"],
            "alt_text": copy["alt"],
            "media_source": {"source_type": "image_base64", "content_type": "image/jpeg",
                             "data": base64.b64encode(img).decode()},
        }
        data = check(self.s.post(f"{self.api}/pins", json=body, timeout=60), "pin")
        pid = data["id"]
        return pid, self.cfg["pin_url"].format(id=pid)

    def metrics(self, posts) -> dict[int, dict]:
        self.token()
        out = {}
        end = date.today()
        start = end - timedelta(days=89)
        for p in posts:
            r = self.s.get(f"{self.api}/pins/{p['remote_id']}/analytics", params={
                "start_date": start.isoformat(), "end_date": end.isoformat(),
                "metric_types": "IMPRESSION,OUTBOUND_CLICK,SAVE,PIN_CLICK"}, timeout=30)
            if r.status_code != 200:
                continue
            data = r.json().get("all", {})
            summ = data.get("summary_metrics") or data.get("lifetime_metrics") or {}
            if not summ and data.get("daily_metrics"):
                summ = {}
                for d in data["daily_metrics"]:
                    for k, v in (d.get("metrics") or {}).items():
                        summ[k] = summ.get(k, 0) + (v or 0)
            out[p["id"]] = {k.lower(): v for k, v in summ.items() if isinstance(v, (int, float))}
        return out


class PinWebhook:
    name = "pin"

    def __init__(self, settings: dict):
        self.settings = settings

    def publish(self, post, product, category, copy) -> tuple[str | None, str | None]:
        from ..boards import board_name, board_specs
        name = board_name(category, self.settings)
        tok = env("PIN_WEBHOOK_TOKEN", required=False)
        r = requests.post(env("PIN_WEBHOOK_URL"), headers={"X-App-Token": tok} if tok else {}, json={
            "board": name,
            "board_description": board_specs([category], self.settings)[name]["description"],
            "title": copy["title"],
            "description": copy["description"],
            "link": post["link"],
            "image_url": product["image_url"],
            "alt_text": copy["alt"],
            "keywords": copy["tags"],
            "post_id": post["id"],
        }, timeout=60)
        data = check(r, "pin-webhook")
        pid = str(data.get("id") or "") if isinstance(data, dict) else ""
        return (pid, self.settings["platforms"]["pin"]["pin_url"].format(id=pid)) if pid else (None, None)

    def metrics(self, posts) -> dict[int, dict]:
        return {}


BOARD_HEADER = ["Board name", "Description", "Keywords", "Suggested cover image", "Cover product", "Categories"]


def export_boards_csv(specs: dict, covers: dict, used: set[str], out_dir: Path, stamp: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    f = out_dir / f"pin_boards_{stamp}.csv"
    with open(f, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=BOARD_HEADER)
        w.writeheader()
        for name in sorted(used):
            s, c = specs[name], covers.get(name, {})
            w.writerow({"Board name": name, "Description": s["description"], "Keywords": s["keywords"],
                        "Suggested cover image": c.get("image_url", ""), "Cover product": c.get("url", ""),
                        "Categories": ", ".join(s["categories"])})
    return f


CSV_HEADER = ["Title", "Media URL", "Board", "Thumbnail", "Description", "Link", "Publish date", "Keywords"]


def export_csv(rows: list[dict], out_dir: Path, stamp: str, board_column: str = "Board") -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    header = [board_column if h == "Board" else h for h in CSV_HEADER]
    files = []
    for i in range(0, len(rows), 100):
        f = out_dir / f"pin_bulk_{stamp}_{i // 100 + 1}.csv"
        with open(f, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            w.writerows([r[h] for h in CSV_HEADER] for r in rows[i:i + 100])
        files.append(f)
    return files
