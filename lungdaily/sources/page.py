"""Fetch the readable text of a news page.

News feeds (Google News, Bing News) give little more than a headline, which is too thin for an
article — e.g. "X appointed president of Y". Before writing, short news items get the page's
paragraph text added so the writer and fact-checker work from the full story.
"""
from __future__ import annotations

import base64
import html
import json
import re
from urllib.parse import parse_qs, quote, urlparse

import requests

from ..util import log

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36 LungDaily/1.0 (+https://lungdaily.org)")
MAX_CHARS = 4000


def _gnews_id(url: str) -> str:
    p = urlparse(url or "")
    if "news.google." not in p.netloc:
        return ""
    parts = [x for x in p.path.split("/") if x]
    if "articles" in parts and parts.index("articles") + 1 < len(parts):
        return parts[parts.index("articles") + 1]
    if "read" in parts and parts.index("read") + 1 < len(parts):
        return parts[parts.index("read") + 1]
    return ""


def _gnews_offline(gid: str) -> str:
    """Older Google News ids carry the article URL inside the base64 payload."""
    try:
        raw = base64.urlsafe_b64decode(gid + "=" * (-len(gid) % 4))
    except (ValueError, TypeError):
        return ""
    m = re.search(rb"https?://[\x21-\x7e]+", raw)
    if not m:
        return ""
    u = m.group(0).decode("ascii", "ignore")
    # a trailing length/field byte can leak in as a printable char after the URL
    return u if "news.google." not in u else ""


def decode_google_news(url: str) -> str:
    """news.google.com/rss/articles/<id> -> the publisher's own URL ("" if it can't be decoded).
    Newer ids (AU_yqL...) need Google's batchexecute call with the page's signature + timestamp."""
    gid = _gnews_id(url)
    if not gid:
        return ""
    u = _gnews_offline(gid)
    if u:
        return u
    try:
        sg = ts = None
        for page in (f"https://news.google.com/articles/{gid}", f"https://news.google.com/rss/articles/{gid}"):
            r = requests.get(page, timeout=20, headers={"User-Agent": UA, "Accept-Language": "en"})
            if r.ok:
                sg = re.search(r'data-n-a-sg="([^"]+)"', r.text)
                ts = re.search(r'data-n-a-ts="([^"]+)"', r.text)
                if sg and ts:
                    break
        if not (sg and ts):
            log.info("Google News decode: no signature for %s", gid[:30])
            return ""
        inner = ["garturlreq", [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None,
                                 None, None, 0, 1], "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
                 gid, int(ts.group(1)), sg.group(1)]
        freq = json.dumps([[["Fbv4je", json.dumps(inner), None, "generic"]]])
        r = requests.post("https://news.google.com/_/DotsSplashUi/data/batchexecute", timeout=20,
                          headers={"User-Agent": UA,
                                   "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
                          data="f.req=" + quote(freq))
        r.raise_for_status()
        chunk = r.text.split("\n\n", 1)[1]
        u = json.loads(json.loads(chunk)[0][2])[1]
        return u if isinstance(u, str) and u.startswith("http") else ""
    except (requests.RequestException, ValueError, IndexError, KeyError, TypeError) as e:
        log.info("Google News decode failed (%s): %s", gid[:30], e)
        return ""


def real_url(url: str) -> str:
    """Unwrap redirect links (Google News articles, Bing apiclick.aspx?url=..., Gmail google.com/url?q=...)."""
    p = urlparse(url or "")
    if "news.google." in p.netloc:
        return decode_google_news(url) or url
    if p.netloc.endswith("google.com") and p.path == "/url":   # Gmail/Google link wrapper
        q = parse_qs(p.query)
        u = q.get("q") or q.get("url")
        if u and u[0].startswith("http"):
            return u[0]
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
