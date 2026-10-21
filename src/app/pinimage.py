from __future__ import annotations

import hashlib
import io
import textwrap
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

W, H = 1000, 1500
PHOTO_H = 1130
BG = (250, 247, 242)
INK = (34, 34, 40)
ACCENT = (180, 70, 60)
TEMPLATES = ("band", "price", "frame", "minimal")
PALETTES = [((180, 70, 60), (250, 247, 242)), ((46, 94, 78), (244, 247, 242)), ((52, 72, 125), (243, 245, 250)),
            ((150, 98, 30), (251, 247, 238)), ((120, 60, 120), (248, 244, 249))]

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


def pick(seed: str, n: int, salt: int = 0) -> int:
    return (int(hashlib.md5(seed.encode()).hexdigest(), 16) + salt) % n


def _fit(photo: Image.Image, box_w: int, box_h: int) -> Image.Image:
    scale = min(box_w / photo.width, box_h / photo.height)
    return photo.resize((max(1, int(photo.width * scale)), max(1, int(photo.height * scale))), Image.LANCZOS)


def _centered(d: ImageDraw.ImageDraw, text: str, y: int, font, fill, width: int = W):
    tw = d.textlength(text, font=font)
    d.text(((width - tw) / 2, y), text, font=font, fill=fill)


def _title_lines(title: str, width: int, n: int) -> list[str]:
    lines = textwrap.wrap(title, width=width)
    if len(lines) > n:
        lines = lines[:n]
        lines[-1] = lines[-1].rstrip(" ,.-") + "…"
    return lines


def compose(photo: Image.Image, title: str, footer: str = "Personalize it", template: str = "band",
            price: str | None = None, palette: int = 0) -> Image.Image:
    acc, bg = PALETTES[palette % len(PALETTES)]
    if template == "minimal":
        canvas = Image.new("RGB", (W, H), (255, 255, 255))
        ph = _fit(photo, W - 40, H - 200)
        canvas.paste(ph, ((W - ph.width) // 2, 20 + (H - 200 - ph.height) // 2))
        d = ImageDraw.Draw(canvas)
        f = _font(40)
        lines = _title_lines(title, 38, 2)
        y = H - 160 + (2 - len(lines)) * 26
        for ln in lines:
            _centered(d, ln, y, f, INK)
            y += 52
        return canvas
    if template == "frame":
        canvas = Image.new("RGB", (W, H), acc)
        inner = Image.new("RGB", (W - 60, H - 60), bg)
        canvas.paste(inner, (30, 30))
        d = ImageDraw.Draw(canvas)
        f_title, f_foot = _font(50), _font(32)
        y = 70
        for ln in _title_lines(title, 30, 2):
            _centered(d, ln, y, f_title, INK)
            y += 64
        top = y + 20
        ph = _fit(photo, W - 140, H - top - 150)
        canvas.paste(ph, ((W - ph.width) // 2, top + (H - top - 150 - ph.height) // 2))
        _centered(d, footer, H - 115, f_foot, acc)
        return canvas
    canvas = Image.new("RGB", (W, H), bg)
    ph = _fit(photo, W - 60, PHOTO_H - 60)
    canvas.paste(ph, ((W - ph.width) // 2, 30 + (PHOTO_H - 60 - ph.height) // 2))
    d = ImageDraw.Draw(canvas)
    d.rectangle([0, PHOTO_H, W, H], fill=(255, 255, 255))
    d.rectangle([0, PHOTO_H, W, PHOTO_H + 8], fill=acc)
    f_title, f_foot = _font(52), _font(34)
    y = PHOTO_H + 45
    for ln in _title_lines(title, 30, 3):
        _centered(d, ln, y, f_title, INK)
        y += 66
    _centered(d, footer, H - 80, f_foot, acc)
    if template == "price" and price:
        f_price = _font(46)
        tw = int(d.textlength(price, font=f_price))
        x0, y0 = W - tw - 90, 50
        d.rounded_rectangle([x0, y0, W - 40, y0 + 84], radius=42, fill=acc)
        d.text((x0 + 25, y0 + 15), price, font=f_price, fill=(255, 255, 255))
    return canvas


def collage(photos: list[Image.Image], headline: str, footer: str, palette: int = 0) -> Image.Image:
    acc, bg = PALETTES[palette % len(PALETTES)]
    canvas = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(canvas)
    d.rectangle([0, 0, W, 230], fill=acc)
    f_head, f_foot = _font(58), _font(34)
    lines = _title_lines(headline, 26, 2)
    y = 115 - len(lines) * 34
    for ln in lines:
        _centered(d, ln, y, f_head, (255, 255, 255))
        y += 70
    n = len(photos)
    cols = 3 if n >= 7 else 2
    rows = min(3, max(1, (n + cols - 1) // cols))
    gap, top, bottom = 18, 250, 120
    cw = (W - gap * (cols + 1)) // cols
    ch = (H - top - bottom - gap * (rows - 1)) // rows
    for i, p in enumerate(photos[:cols * rows]):
        r, c = divmod(i, cols)
        cell = Image.new("RGB", (cw, ch), (255, 255, 255))
        ph = _fit(p, cw - 16, ch - 16)
        cell.paste(ph, ((cw - ph.width) // 2, (ch - ph.height) // 2))
        canvas.paste(cell, (gap + c * (cw + gap), top + r * (ch + gap)))
    _centered(d, footer, H - 85, f_foot, acc)
    return canvas


def story(card: Image.Image, size: tuple[int, int] = (1080, 1920), palette: int = 0) -> Image.Image:
    acc, bg = PALETTES[palette % len(PALETTES)]
    canvas = Image.new("RGB", size, bg)
    fitted = ImageOps.contain(card, (size[0] - 60, size[1] - 160))
    canvas.paste(fitted, ((size[0] - fitted.width) // 2, (size[1] - fitted.height) // 2))
    d = ImageDraw.Draw(canvas)
    d.rectangle([0, size[1] - 24, size[0], size[1]], fill=acc)
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


def make_pin(image_url: str, title: str, footer: str = "Personalize it", template: str = "band",
             price: str | None = None, palette: int = 0) -> bytes:
    return to_jpeg_bytes(compose(fetch_image(image_url), title, footer, template, price, palette))
