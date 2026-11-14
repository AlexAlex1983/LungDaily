"""Articles from things people send us.

from_url(url)  — a press release / announcement link (e.g. sent to editor@): the page is read,
                 triaged, written and fact-checked like any other item, then put at the front of
                 the ready queue (next publishing slot). Failing the fact-check -> held, as always.
qa(path)       — an email Q&A (content/qa/<name>.md): answers are published as given, no AI rewriting.
"""
from __future__ import annotations

import hashlib
import html
import re
from urllib.parse import urlparse

import requests
import yaml

from . import process
from .sources import make_item
from .sources.page import UA, extract_text, real_url
from .util import ROOT, log, now, read_json, slugify, write_json

CATEGORIES = ("voices", "research", "policy", "approvals", "people", "events")


def _meta(page: str, *names: str) -> str:
    for n in names:
        m = re.search(rf'<meta[^>]+(?:property|name)=["\']{re.escape(n)}["\'][^>]+content=["\']([^"\']+)', page, re.I) \
            or re.search(rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{re.escape(n)}["\']', page, re.I)
        if m:
            return html.unescape(m.group(1)).strip()
    return ""


def item_from_url(url: str) -> dict | None:
    url = real_url(url.strip())
    try:
        r = requests.get(url, timeout=30, headers={"User-Agent": UA, "Accept-Language": "en"})
        r.raise_for_status()
    except requests.RequestException as e:
        log.error("From URL: could not read %s: %s", url, e)
        return None
    page = r.text
    text = extract_text(page)
    if len(text.split()) < 40:
        log.error("From URL: too little readable text on %s (%d words)", url, len(text.split()))
        return None
    title = _meta(page, "og:title", "twitter:title") or html.unescape(
        (re.search(r"(?is)<title[^>]*>(.*?)</title>", page) or [None, ""])[1]).strip()
    site = _meta(page, "og:site_name") or urlparse(r.url).netloc.replace("www.", "")
    published = _meta(page, "article:published_time", "date", "dc.date") or now().isoformat()
    return make_item("rss", "submitted-" + hashlib.sha1(r.url.encode()).hexdigest()[:16], url=r.url, title=title,
                     text=text, author_name=site, published_at=published,
                     extra={"feed": "Submitted", "submitted": True, "page_fetched": True})


def from_url(url: str, category: str = "") -> str | None:
    """Write one article from a URL straight into the ready queue. Returns the slug (or None)."""
    item = item_from_url(url)
    if not item:
        return None
    process.triage([item])
    t = item.get("triage") or {"score": 7, "category": "voices", "duplicate": False, "reason": "triage failed"}
    if category in CATEGORIES:
        t["category"] = category
    t["score"] = max(float(t.get("score") or 0), 9.5)   # editor's pick: goes out at the next slot
    t["duplicate"] = False
    item["triage"] = t
    log.info("From URL: %s → %s (%s)", item["title"][:90], t["category"], t.get("reason", ""))
    held = read_json(process.HELD, [])
    stats = {"published": 0, "held": 0, "failed": 0}
    slug = process.produce(item, held, stats)
    write_json(process.HELD, held)
    log.info("From URL result: %s", stats)
    return slug or None


def qa(path: str) -> str | None:
    """content/qa/<name>.md -> a ready Q&A article.

    Front matter (YAML between --- lines): name, role, organisation, headline, dek, tags,
    link (their page), consent: yes (they agreed to publication), optional x (handle).
    Body: an intro paragraph, then '## Question' headings each followed by the answer."""
    p = (ROOT / path) if not path.startswith("/") else __import__("pathlib").Path(path)
    raw = p.read_text(encoding="utf-8")
    m = re.match(r"(?s)---\n(.*?)\n---\n(.*)", raw)
    if not m:
        log.error("Q&A: %s has no front matter", path)
        return None
    meta, body = yaml.safe_load(m.group(1)) or {}, m.group(2).strip()
    if str(meta.get("consent", "")).lower() not in ("yes", "true"):
        log.error("Q&A: %s — consent is not 'yes'; not publishing", path)
        return None
    body = re.sub(r"(?m)^## (.+)$", r"**\1**", body)   # questions in bold, answers as plain paragraphs
    name = meta["name"]
    rec = {
        "slug": process._unique_slug(f"{now().date().isoformat()}-{slugify(meta.get('headline') or 'qa-' + name)}"),
        "headline": meta.get("headline") or f"Q&A: {name}",
        "dek": meta.get("dek", ""),
        "body": body,
        "key_quote": meta.get("key_quote", ""),
        "tags": [str(t).lower() for t in meta.get("tags") or []][:5],
        "people": [name],
        "category": "voices",
        "kind": "qa",
        "score": 9.0,
        "created_at": now().isoformat(timespec="seconds"),
        "checker": {"pass": True, "issues": [], "note": "Q&A answers published as given"},
        "embed": {},
        "mentions": [meta["x"].lstrip("@")] if meta.get("x") else [],
        "source": {"id": f"qa:{p.stem}", "source": "qa", "url": meta.get("link", ""),
                   "title": f"{name}, {meta.get('role', '')}{', ' + meta['organisation'] if meta.get('organisation') else ''}".strip(", "),
                   "author_name": name, "author_handle": (meta.get("x") or "").lstrip("@"), "author_url": meta.get("link", ""),
                   "published_at": now().isoformat(timespec="seconds"), "extra": {"feed": "Q&A"}},
    }
    process.save_ready(rec)
    log.info("Q&A ready for the next slot: %s", rec["headline"])
    return rec["slug"]
