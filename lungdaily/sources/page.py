"""Fetch the readable text of a news page.

News feeds (Google News, Bing News) give little more than a headline, which is too thin for an
article — e.g. "X appointed president of Y". Before writing, short news items get the page's
paragraph text added so the writer and fact-checker work from the full story.
"""
from __future__ import annotations

import html
import re
from urllib.parse import parse_qs, urlparse

import requests

from ..util import log

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36 LungDaily/1.0 (+https://lungdaily.org)")
MAX_CHARS = 4000


def real_url(url: str) -> str:
    """Unwrap news-aggregator redirect links (Bing apiclick.aspx?url=...)."""
    p = urlparse(url or "")
    if "bing.com" in p.netloc:
        u = parse_qs(p.query).get("url")
        if u:
            return u[0]
    return url


def extract_text(page_html: str) -> str:
    s = re.sub(r"(?is)<(script|style|noscript|svg|nav|header|footer|aside|form)[^>]*>.*?</\1>", " ", page_html)
    paras = []
    for m in re.finditer(r"(?is)<p[^>]*>(.*?)</p>", s):
        t = re.sub(r"<[^>]+>", " ", m.group(1))
        t = re.sub(r"\s+", " ", html.unescape(t)).strip()
        if len(t) >= 40 and t not in paras:
            paras.append(t)
    out = "\n".join(paras)
    return out[:MAX_CHARS].rsplit(" ", 1)[0] if len(out) > MAX_CHARS else out


def fetch_text(url: str) -> tuple[str, str]:
    """Returns (final_url, text). Empty text when the page can't be read."""
    url = real_url(url)
    if not url.startswith("http"):
        return url, ""
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": UA, "Accept-Language": "en"}, allow_redirects=True)
        r.raise_for_status()
    except requests.RequestException as e:
        log.info("Page fetch failed (%s): %s", url[:80], e)
        return url, ""
    final = r.url
    if "news.google.com" in urlparse(final).netloc:   # JS redirect page, no article text
        return url, ""
    if "html" not in r.headers.get("content-type", "html"):
        return final, ""
    return final, extract_text(r.text)
