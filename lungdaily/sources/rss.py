"""RSS 2.0 / Atom feeds (WHO, Google News queries, journals...)."""
from __future__ import annotations

import hashlib
import html
import re
import xml.etree.ElementTree as ET

import requests

from ..util import iso, load_yaml, log, parse_date, settings
from . import make_item

ATOM = "{http://www.w3.org/2005/Atom}"


def _clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", html.unescape(s or ""))
    return re.sub(r"\s+", " ", s).strip()


def parse_feed(xml_text: str, feed_name: str, limit: int) -> list[dict]:
    root = ET.fromstring(xml_text)
    entries = root.findall(".//item") or root.findall(f".//{ATOM}entry")
    items = []
    for e in entries[:limit]:
        title = _clean(e.findtext("title") or e.findtext(f"{ATOM}title") or "")
        link = e.findtext("link") or ""
        if not link:
            le = e.find(f"{ATOM}link")
            link = le.get("href", "") if le is not None else ""
        desc = _clean(e.findtext("description") or e.findtext(f"{ATOM}summary") or e.findtext(f"{ATOM}content") or "")
        date = parse_date(e.findtext("pubDate") or e.findtext(f"{ATOM}updated") or e.findtext(f"{ATOM}published"))
        source_el = e.find("source")
        if source_el is None:   # Bing News: <News:Source>
            source_el = next((c for c in e if c.tag.lower().endswith("}source")), None)
        publisher = source_el.text if source_el is not None and source_el.text else feed_name
        if publisher and title.endswith(" - " + publisher):   # Google News appends " - Publisher"
            title = title[: -len(publisher) - 3].strip()
        if not title or not link:
            continue
        nid = hashlib.sha1(link.encode()).hexdigest()[:16]
        items.append(make_item(
            "rss", nid, url=link, title=title, text=desc or title,
            author_name=publisher, published_at=iso(date), extra={"feed": feed_name},
        ))
    return items


def collect() -> list[dict]:
    lim = settings()["limits"]["rss_max_per_feed"]
    items = []
    for feed in load_yaml("topics.yaml").get("rss_feeds", []):
        try:
            r = requests.get(feed["url"], timeout=30, headers={"User-Agent": "LungDaily/1.0 (+news aggregator)"})
            r.raise_for_status()
            got = parse_feed(r.text, feed["name"], lim)
            items += got
            log.info("RSS %s: %d", feed["name"], len(got))
        except (requests.RequestException, ET.ParseError) as e:
            log.warning("RSS %s failed: %s", feed["name"], e)
    return items
