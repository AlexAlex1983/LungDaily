"""Source figures: when the original article can be read and openly licensed, take one data figure
(a chart, graph, map or table image — never a photo) and show it in the article with its caption,
credit and licence.

Rights rule: a figure is only reused when the page states an open licence that allows reuse
(CC BY / CC BY-SA / CC BY-ND, not NonCommercial) or the domain is listed in
figures.public_domain_domains (e.g. US government sites). Anything else is left alone.
"""
from __future__ import annotations

import html
import io
import re
from urllib.parse import urljoin, urlparse

import requests
from PIL import Image

from .sources.page import UA, real_url
from .util import ROOT, log, settings

FIGURES = ROOT / "content" / "figures"

FIG_WORDS = re.compile(r"(?i)\b(fig(ure)?\.?\s*\d+|chart|graph|map|table\s*\d+|infographic|trend|"
                       r"forest plot|kaplan|incidence|prevalence|mortality|concentration)")
PHOTO_WORDS = re.compile(r"(?i)\b(photo|photograph|pictured|portrait|getty|shutterstock|istock|alamy|"
                         r"ap photo|reuters/|afp via|headshot|courtesy of|smiling|patient with|doctor|nurse|"
                         r"man |woman |people|child|children|crowd|logo)\b")
LICENCES = [
    (re.compile(r"creativecommons\.org/licenses/by/(\d\.\d)"), "CC BY {}"),
    (re.compile(r"creativecommons\.org/licenses/by-sa/(\d\.\d)"), "CC BY-SA {}"),
    (re.compile(r"creativecommons\.org/licenses/by-nd/(\d\.\d)"), "CC BY-ND {}"),
]


def licence_of(page_html: str, url: str, cfg: dict | None = None) -> str:
    """Reuse licence of the page, or '' when reuse isn't clearly allowed."""
    cfg = cfg or (settings().get("figures") or {})
    host = urlparse(url).netloc.lower()
    for d in cfg.get("public_domain_domains") or []:
        if host == d or host.endswith("." + d):
            return "Public domain"
    if re.search(r"creativecommons\.org/licenses/by-nc", page_html):
        return ""                                  # NonCommercial: LungDaily may carry ads/sponsors
    for rx, label in LICENCES:
        m = rx.search(page_html)
        if m:
            return label.format(m.group(1))
    return ""


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def candidates(page_html: str, base_url: str) -> list[dict]:
    """Data figures on the page: <figure> blocks whose caption reads like a chart, not a photo."""
    out = []
    for m in re.finditer(r"(?is)<figure\b[^>]*>(.*?)</figure>", page_html):
        block = m.group(1)
        img = re.search(r'(?is)<img\b[^>]*?\s(?:data-src|src)="([^"]+)"[^>]*>', block)
        if not img:
            continue
        alt = re.search(r'(?is)\salt="([^"]*)"', img.group(0))
        cap = re.search(r"(?is)<figcaption[^>]*>(.*?)</figcaption>", block)
        caption = _clean(cap.group(1) if cap else "") or _clean(alt.group(1) if alt else "")
        src = urljoin(base_url, html.unescape(img.group(1)))
        if not caption or not FIG_WORDS.search(caption) or PHOTO_WORDS.search(caption):
            continue
        if src.lower().endswith(".svg") or src.startswith("data:"):
            continue
        out.append({"src": src, "caption": caption[:400]})
    return out


def grab(item: dict, slug: str, fetch=None, download=None) -> dict | None:
    """Save one source figure for this article; returns the figure record or None."""
    cfg = settings().get("figures") or {}
    if not cfg.get("enabled") or item.get("source") != "rss":
        return None
    url = real_url(item.get("url") or "")
    if not url.startswith("http"):
        return None
    try:
        if fetch:
            final, page = fetch(url)
        else:
            r = requests.get(url, timeout=20, headers={"User-Agent": UA, "Accept-Language": "en"})
            r.raise_for_status()
            final, page = r.url, r.text
    except requests.RequestException as e:
        log.info("Figure: page fetch failed (%s): %s", url[:80], e)
        return None
    lic = licence_of(page, final, cfg)
    if not lic:
        return None
    for c in candidates(page, final)[:3]:
        try:
            data = download(c["src"]) if download else requests.get(
                c["src"], timeout=30, headers={"User-Agent": UA, "Referer": final}).content
            img = Image.open(io.BytesIO(data))
            img.load()
        except Exception as e:                       # noqa: BLE001 - any bad image is just skipped
            log.info("Figure: image unusable (%s): %s", c["src"][:80], e)
            continue
        if img.width < int(cfg.get("min_width", 500)) or img.height < 250:
            continue
        FIGURES.mkdir(parents=True, exist_ok=True)
        img = img.convert("RGB")
        if img.width > 1400:
            img = img.resize((1400, round(img.height * 1400 / img.width)))
        img.save(FIGURES / f"{slug}.jpg", "JPEG", quality=88)
        rec = {"file": f"{slug}.jpg", "caption": c["caption"], "credit": item.get("author_name") or urlparse(final).netloc,
               "licence": lic, "source_url": final}
        log.info("Figure saved for %s (%s, %s)", slug, lic, c["caption"][:60])
        return rec
    return None
