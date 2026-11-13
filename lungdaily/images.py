"""Branded share images (1200x630) for every article: shown on the site and as X / LinkedIn link cards.

By default: original graphics only — headline, category, source and the LungDaily mark.
If the editor supplies a picture for an article (content/images/<slug>.jpg|png|webp), that picture
becomes the background of the thumbnail and the share card, with the LungDaily branding on top.
Only pictures the editor has the right to use go in that folder.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

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
    if variant == "thumb":
        # site thumbnail: clean — the page already shows category and brand around it
        name = source_label(rec)
        f, lines, lh = fit_headline(name, W - 2 * pad - 160, 300)
        y = (H - len(lines) * lh) // 2
        for line in lines:
            d.text((pad, y), line, font=f, fill=WHITE)
            y += lh
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(out, "PNG", optimize=True)
        return out
    else:
        # category pill
        kf = font("SemiBold", 24)
        label = category_label.upper()
        tw = kf.getlength(label)
        d.rounded_rectangle([pad, 64, pad + tw + 36, 64 + 44], radius=22, fill=CORAL)
        d.text((pad + 18, 64 + 22), label, font=kf, fill=WHITE, anchor="lm")
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


IMAGES = ROOT / "content" / "images"
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def custom_image(slug: str) -> Path | None:
    """Editor-supplied picture for an article, if any."""
    for ext in IMAGE_EXTS:
        p = IMAGES / f"{slug}{ext}"
        if p.exists():
            return p
    return None


def add_image(slug: str, url: str, credit: str = "") -> Path:
    """Download a picture (that the editor has the right to use) as the picture for an article."""
    import io
    import urllib.request

    slug = slug.strip()
    if not (ROOT / "content" / "posts" / f"{slug}.json").exists():
        raise SystemExit(f"No article with slug {slug!r}")
    req = urllib.request.Request(url.strip(), headers={"User-Agent": "LungDaily-bot/1.0 (+https://lungdaily.org)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    img = Image.open(io.BytesIO(data))
    img.load()
    if img.width < 600 or img.height < 300:
        raise SystemExit(f"Picture too small: {img.width}x{img.height}")
    IMAGES.mkdir(parents=True, exist_ok=True)
    for ext in IMAGE_EXTS:
        old = IMAGES / f"{slug}{ext}"
        if old.exists():
            old.unlink()
    out = IMAGES / f"{slug}.jpg"
    img.convert("RGB").save(out, "JPEG", quality=90)
    credit_file = IMAGES / f"{slug}.credit.txt"
    if credit.strip():
        credit_file.write_text(credit.strip() + "\n", encoding="utf-8")
    print(f"Saved {out.name} ({img.width}x{img.height}); credit: {credit.strip() or '-'}")
    return out


def custom_credit(slug: str) -> str:
    """Optional credit line for an editor picture: content/images/<slug>.credit.txt."""
    p = IMAGES / f"{slug}.credit.txt"
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


def render_custom(rec: dict, src: Path, out: Path, category_label: str, variant: str = "share") -> Path:
    """Editor picture, cover-cropped to 1200x630, with category pill and LungDaily footer.
    variant "share" also puts the headline over a dark gradient; "thumb" keeps the picture clean."""
    base = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
    # portrait pictures: keep the upper part (faces sit near the top); landscape: near centre
    cy = 0.15 if base.height > base.width else 0.4
    img = ImageOps.fit(base, (W, H), method=Image.LANCZOS, centering=(0.5, cy))
    bg = CATEGORY_BG.get(rec.get("category"), CATEGORY_BG["voices"])
    if variant == "thumb":
        # site thumbnail: the picture alone — category and brand are shown by the page itself
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(out, "PNG", optimize=True)
        return out

    # dark gradient from the bottom so text stays readable on any picture
    grad_h = 420 if variant == "share" else 150
    grad = Image.new("L", (1, grad_h))
    for y in range(grad_h):
        grad.putpixel((0, y), int(225 * (y / grad_h) ** 1.4))
    alpha = grad.resize((W, grad_h))
    shade = Image.new("RGB", (W, grad_h), (8, 26, 30))
    img.paste(shade, (0, H - grad_h), alpha)
    d = ImageDraw.Draw(img)

    pad = 72
    kf = font("SemiBold", 24)
    label = category_label.upper()
    tw = kf.getlength(label)
    d.rounded_rectangle([pad, 56, pad + tw + 36, 56 + 44], radius=22, fill=CORAL)
    d.text((pad + 18, 56 + 22), label, font=kf, fill=WHITE, anchor="lm")

    fy = H - 56
    if variant == "share":
        f, lines, lh = fit_headline(rec["headline"], W - 2 * pad, 200)
        y = fy - 40 - len(lines) * lh
        for line in lines:
            d.text((pad, y), line, font=f, fill=WHITE)
            y += lh
    draw_mark(d, pad + 20, fy, 20, fill=WHITE, bg=(8, 26, 30))
    bf = font("SemiBold", 30)
    d.text((pad + 54, fy), "Lung", font=bf, fill=WHITE, anchor="lm")
    d.text((pad + 54 + bf.getlength("Lung"), fy), "Daily", font=font("Regular", 30), fill=SOFT, anchor="lm")
    d.text((W - pad, fy), "lungdaily.org", font=font("Medium", 24), fill=SOFT, anchor="rm")

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG", optimize=True)
    return out


# ---------------------------------------------------------------- picture library
# content/library/<theme>/*.jpg: original illustrations drawn by scripts/make_picture_library.py
# (no photos, no people). Articles without an editor picture get one, matched by topic, rotated so
# the least recently used picture of the theme comes next; the choice is kept in data/library.json.
LIBRARY = ROOT / "content" / "library"
LIBRARY_STATE = ROOT / "data" / "library.json"

CATEGORY_THEME = {"voices": "lungs", "research": "research", "policy": "globe",
                  "approvals": "pharma", "people": "lungs", "events": "events"}


def library_index() -> dict:
    import json
    p = LIBRARY / "index.json"
    if not p.exists():
        return {}
    idx = json.loads(p.read_text(encoding="utf-8"))
    return {t: v for t, v in idx.items() if any((LIBRARY / t / f).exists() for f in v.get("files", []))}


def rank_themes(rec: dict, index: dict) -> list[str]:
    """Themes ordered by keyword hits in headline/dek/tags/body (headline counts double).
    The category default gets +0.5, so it wins ties and comes first when nothing matches.
    Only themes with a hit (plus the default) are returned."""
    head = f" {rec.get('headline', '')} ".lower()
    rest = f" {rec.get('dek', '')} {' '.join(rec.get('tags') or [])} {(rec.get('body') or '')[:600]} ".lower()
    default = CATEGORY_THEME.get(rec.get("category"), "lungs")
    scores = {}
    for theme, info in index.items():
        score = 0.0
        for kw in info.get("keywords", []):
            kw = kw.lower()
            score += 2 * head.count(kw) + rest.count(kw)
        if theme == default:
            score += 0.5
        if score > 0:
            scores[theme] = score
    ranked = sorted(scores, key=lambda t: -scores[t])
    return ranked or ([default] if default in index else list(index)[:1])


def pick_theme(rec: dict, index: dict) -> str:
    """Best-matching theme (see rank_themes)."""
    ranked = rank_themes(rec, index)
    return ranked[0] if ranked else ""


def _stable(text: str) -> int:
    import hashlib
    return int(hashlib.sha1(text.encode()).hexdigest()[:8], 16)


# a picture used within the last RECENT_WINDOW assignments (~2 days at 16 articles/day) is
# avoided: the next-best matching theme is tried first, then the generic fallback themes
RECENT_WINDOW = 32
FALLBACK_THEMES = ("lungs", "research", "data", "nature")


def library_picture(rec: dict, state: dict, index: dict, share: float = 1.0) -> Path | None:
    """Library picture for an article, or None (keep the plain branded card).
    `state` is data/library.json content and is updated in place."""
    if not index:
        return None
    slug = rec["slug"]
    assigned = state.setdefault("assigned", {})
    if slug in assigned:
        name = assigned[slug]
        if name and (LIBRARY / name).exists():
            return LIBRARY / name
        if not name and share < 1.0:
            return None  # kept the plain card earlier; at share 1.0 it gets a picture now
    if share < 1.0 and (_stable("share:" + slug) % 1000) / 1000 >= share:
        assigned[slug] = ""
        return None
    last = state.setdefault("last_used", {})
    counter = state.get("counter", 0)

    def lru(theme):
        files = [f for f in index.get(theme, {}).get("files", []) if (LIBRARY / theme / f).exists()]
        if not files:
            return None, None
        files.sort(key=lambda f: (last.get(f"{theme}/{f}", -1), _stable(slug + f)))
        return f"{theme}/{files[0]}", last.get(f"{theme}/{files[0]}", -1)

    ranked = rank_themes(rec, index)
    candidates = ranked + [t for t in FALLBACK_THEMES if t not in ranked]
    name = None
    for theme in candidates:
        cand, used = lru(theme)
        if cand and (used < 0 or counter - used >= RECENT_WINDOW):
            name = cand
            break
    if name is None:  # everything used recently: least recently used of the best theme
        for theme in candidates:
            name, _ = lru(theme)
            if name:
                break
    if name is None:
        return None
    state["counter"] = counter + 1
    last[name] = state["counter"]
    assigned[slug] = name
    return LIBRARY / name
