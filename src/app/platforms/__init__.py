from .base import AuthError, PlatformError, RateLimited


def get(name: str, settings: dict):
    if name == "pin":
        from .pin import Pin, PinWebhook
        mode = settings["platforms"]["pin"].get("mode")
        return PinWebhook(settings) if mode == "webhook" else Pin(settings)
    if name == "tumblr":
        from .tumblr import Tumblr
        return Tumblr(settings)
    if name == "bluesky":
        from .bluesky import Bluesky
        return Bluesky(settings)
    if name == "threads":
        from .threads import Threads
        return Threads(settings)
    raise KeyError(name)
