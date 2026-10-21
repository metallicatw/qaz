from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = Path(os.environ.get("APP_CONFIG_DIR", ROOT / "config"))
STATE_DIR = Path(os.environ.get("APP_STATE_DIR", ROOT / "state"))
SITE_DIR = Path(os.environ.get("APP_SITE_DIR", ROOT / "site"))
DATA_DIR = Path(os.environ.get("APP_DATA_DIR", ROOT / "data"))


@dataclass
class Category:
    key: str
    qs: str
    dept: str = "Gifts"
    board: str | None = None
    tags: list = field(default_factory=list)
    season: list[str] | None = None
    weight: float = 1.0
    board_desc: str | None = None

    def in_season(self, today: date) -> bool | None:
        if not self.season:
            return None
        (sm, sd), (em, ed) = [tuple(int(x) for x in s.split("/")) for s in self.season]
        start, end, t = (sm, sd), (em, ed), (today.month, today.day)
        if start <= end:
            return start <= t <= end
        return t >= start or t <= end

    def effective_weight(self, today: date) -> float:
        s = self.in_season(today)
        if s is None:
            return self.weight
        return self.weight * (3.0 if s else 0.3)


def load_settings(path: Path | None = None) -> dict:
    with open(path or CONFIG_DIR / "settings.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_categories(path: Path | None = None) -> list[Category]:
    with open(path or CONFIG_DIR / "categories.yaml", encoding="utf-8") as f:
        raw = yaml.safe_load(f)["categories"]
    cats = [Category(**c) for c in raw]
    keys = [c.key for c in cats]
    dup = {k for k in keys if keys.count(k) > 1}
    if dup:
        raise ValueError(f"duplicate category keys: {dup}")
    return cats
