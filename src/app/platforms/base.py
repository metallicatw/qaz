from __future__ import annotations

import os

import requests


class PlatformError(Exception):
    pass


class RateLimited(PlatformError):
    """Platform said slow down -> stop this platform for the current run and back off."""


class AuthError(PlatformError):
    """Credentials missing/expired -> needs human action, reported prominently."""


def env(name: str, required: bool = True) -> str | None:
    v = os.environ.get(name)
    if required and not v:
        raise AuthError(f"missing secret {name}")
    return v


def check(r: requests.Response, platform: str) -> dict:
    if r.status_code == 429:
        raise RateLimited(f"{platform} 429: {r.text[:200]}")
    if r.status_code in (401, 403):
        raise AuthError(f"{platform} {r.status_code}: {r.text[:300]}")
    if r.status_code >= 400:
        raise PlatformError(f"{platform} {r.status_code}: {r.text[:300]}")
    try:
        return r.json()
    except ValueError:
        return {}
