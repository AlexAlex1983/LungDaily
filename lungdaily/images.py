"""Branded share images (1200x630) for every article: shown on the site and as X / LinkedIn link cards.

Original graphics only — headline, category, source and the LungDaily mark. No photos of people,
no AI-generated faces, nothing copied from the source.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .util import ROOT

W, H = 1200, 630
FONTS = ROOT / "static" / "fonts"
CORAL = (228, 87, 46)
WHITE = (255, 255, 255)
SOFT = (214, 236, 237)

CATEGORY_BG = {
    "voices": (11, 110, 115),
    "research": (39, 70, 128),
    "policy": (52, 94, 37),
    "approvals": (130, 74, 8),
    "people": (104, 45, 110),
    "events": (138, 44, 58),
}

_font_cache: dict = {}


def font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    key = (weight, size)
    if key not in _font_cache:
        path = FONTS / f"Inter-{weight}.otf"
        _font_cache[key] = ImageFont.truetype(str(path), size) if path.exists() else ImageFont.load_default(size)
    return _font_cache[key]


def draw_mark(draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float, fill, bg, accent=CORAL) -> None:
    """The Daybreak mark: two lung halves with a rising sun between them."""
    box = [cx - r, cy - r, cx + r, cy + r]
    draw.pieslice(box, 90, 270, fill=fill)
    draw.pieslice(box, 270, 90, fill=fill)
    g = r * 0.104
    draw.rectangle([cx - g, cy - r - 2, cx + g, cy + r + 2], fill=bg)
    ny, nr, dr = cy - r * 0.917, r * 0.375, r * 0.208
    draw.ellipse([cx - nr, ny - nr, cx + nr, ny + nr], fill=bg)
    draw.ellipse([cx - dr, ny - dr, cx + dr, ny + dr], fill=accent)


def wrap(text: str, f: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if f.getlength(trial) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def fit_headline(text: str, max_w: int, max_h: int):
    for size in (68, 62, 56, 50, 46, 42, 38):
        f = font("Bold", size)
        lines = wrap(text, f, max_w)
        lh = int(size * 1.18)
        if len(lines) * lh <= max_h:
            return f, lines, lh
    f = font("Bold", 38)
    lh = int(38 * 1.18)
    lines = wrap(text, f, max_w)[: max_h // lh]
    lines[-1] = lines[-1].rstrip(".,;: ") + "…"
    return f, lines, lh


def source_label(rec: dict) -> str:
    src = rec.get("source") or {}
    extra = src.get("extra") or {}
    if src.get("source") == "pubmed" and extra.get("journal"):
        return extra["journal"]
    return src.get("author_name") or "LungDaily"


def render(rec: dict, out: Path, category_label: str, variant: str = "share") -> Path:
    """variant "share": headline card for X/LinkedIn. variant "thumb": source-name card for the site."""
    bg = CATEGORY_BG.get(rec.get("category"), CATEGORY_BG["voices"])
    img = Image.new("RGB", (W, H), bg)

    # large faint watermark mark, bottom right
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    draw_mark(ld, 1080, 520, 300, fill=(255, 255, 255, 22), bg=(0, 0, 0, 0), accent=(255, 255, 255, 0))
    img.paste(layer, (0, 0), layer)
    d = ImageDraw.Draw(img)

    pad = 72
    # category pill
    kf = font("SemiBold", 24)
    label = category_label.upper()
    tw = kf.getlength(label)
    d.rounded_rectangle([pad, 64, pad + tw + 36, 64 + 44], radius=22, fill=CORAL)
    d.text((pad + 18, 64 + 22), label, font=kf, fill=WHITE, anchor="lm")

    if variant == "thumb":
        name = source_label(rec)
        f, lines, lh = fit_headline(name, W - 2 * pad - 160, 300)
        y = 150 + max(0, (300 - len(lines) * lh) // 2)
        for line in lines:
            d.text((pad, y), line, font=f, fill=WHITE)
            y += lh
    else:
        f, lines, lh = fit_headline(rec["headline"], W - 2 * pad - 60, 330)
        y = 150
        for line in lines:
            d.text((pad, y), line, font=f, fill=WHITE)
            y += lh

    # footer: brand left, source right
    fy = H - 70
    d.line([pad, fy - 30, W - pad, fy - 30], fill=(255, 255, 255), width=1)
    draw_mark(d, pad + 20, fy + 6, 20, fill=WHITE, bg=bg)
    bf = font("SemiBold", 30)
    d.text((pad + 54, fy + 6), "Lung", font=bf, fill=WHITE, anchor="lm")
    d.text((pad + 54 + bf.getlength("Lung"), fy + 6), "Daily", font=font("Regular", 30), fill=SOFT, anchor="lm")
    src = (rec.get("source") or {}).get("author_name") or ""
    if variant == "thumb":
        d.text((W - pad, fy + 6), "lungdaily.org", font=font("Medium", 24), fill=SOFT, anchor="rm")
    elif src:
        sf = font("Medium", 24)
        src_text = f"Source: {src}"
        while sf.getlength(src_text) > 640 and len(src_text) > 12:
            src_text = src_text[:-2].rstrip() + "…" if not src_text.endswith("…") else src_text[:-2] + "…"
        d.text((W - pad, fy + 6), src_text, font=sf, fill=SOFT, anchor="rm")

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG", optimize=True)
    return out
