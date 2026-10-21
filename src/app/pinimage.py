from __future__ import annotations

import io
import textwrap
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

W, H = 1000, 1500
PHOTO_H = 1130
BG = (250, 247, 242)
INK = (34, 34, 40)
ACCENT = (180, 70, 60)

_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]


def _font(size: int):
    for p in _FONT_CANDIDATES:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size=size)


def fetch_image(url: str, timeout=30) -> Image.Image:
    r = requests.get(url, timeout=timeout, headers={"User-Agent": "app/1.0"})
    r.raise_for_status()
    return Image.open(io.BytesIO(r.content)).convert("RGB")


def compose(photo: Image.Image, title: str, footer: str = "Personalize it") -> Image.Image:
    canvas = Image.new("RGB", (W, H), BG)
    mw, mh = W - 60, PHOTO_H - 60
    scale = min(mw / photo.width, mh / photo.height)
    ph = photo.resize((max(1, int(photo.width * scale)), max(1, int(photo.height * scale))), Image.LANCZOS)
    canvas.paste(ph, ((W - ph.width) // 2, 30 + (PHOTO_H - 60 - ph.height) // 2))
    d = ImageDraw.Draw(canvas)
    d.rectangle([0, PHOTO_H, W, H], fill=(255, 255, 255))
    d.rectangle([0, PHOTO_H, W, PHOTO_H + 8], fill=ACCENT)
    f_title, f_foot = _font(52), _font(34)
    lines = textwrap.wrap(title, width=30)[:3]
    y = PHOTO_H + 45
    for ln in lines:
        tw = d.textlength(ln, font=f_title)
        d.text(((W - tw) / 2, y), ln, font=f_title, fill=INK)
        y += 66
    tw = d.textlength(footer, font=f_foot)
    d.text(((W - tw) / 2, H - 80), footer, font=f_foot, fill=ACCENT)
    return canvas


def to_jpeg_bytes(img: Image.Image, max_bytes: int | None = None) -> bytes:
    q = 88
    while True:
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=q, optimize=True)
        data = buf.getvalue()
        if max_bytes is None or len(data) <= max_bytes or q <= 40:
            return data
        q -= 8


def make_pin(image_url: str, title: str, footer: str = "Personalize it") -> bytes:
    return to_jpeg_bytes(compose(fetch_image(image_url), title, footer))
