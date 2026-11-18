"""Fetch the editor's digest picks from Gmail.

The 08:00/15:00 digest task cannot write to GitHub, so it emails its LungDaily picks to the
editor's own Gmail with the subject "[LungDaily picks] <date> <time>" and the picks JSON as the
body. This reads only those emails (from the same address, last 3 days), read-only, and saves
each one as content/digest/<date>-<time>.json for the digest source.

Secrets: GMAIL_USER (the Gmail address) and GMAIL_APP_PASSWORD (a Google app password).
"""
from __future__ import annotations

import datetime as dt
import email
import imaplib
import json
import os
import re
from email.header import decode_header, make_header
from email.utils import parseaddr

from ..util import DATA, ROOT, log, now, read_json, write_json

DIGEST = ROOT / "content" / "digest"
SEEN = DATA / "gmail_digest_seen.json"
SUBJECT = "[LungDaily picks]"


def _all_mail(imap: imaplib.IMAP4_SSL) -> str:
    """Name of the 'All Mail' folder (localised, e.g. '[Gmail]/Alle Nachrichten'); INBOX as fallback."""
    _, boxes = imap.list()
    for b in boxes or []:
        line = b.decode(errors="ignore")
        if "\\All" in line:
            m = re.search(r'"([^"]+)"\s*$', line) or re.search(r"(\S+)\s*$", line)
            if m:
                return m.group(1)
    return "INBOX"


def _body(msg: email.message.Message) -> str:
    parts = msg.walk() if msg.is_multipart() else [msg]
    plain = html = ""
    for p in parts:
        ctype = p.get_content_type()
        if p.get_content_maintype() == "multipart":
            continue
        payload = p.get_payload(decode=True) or b""
        text = payload.decode(p.get_content_charset() or "utf-8", errors="replace")
        if ctype == "text/plain" and not plain:
            plain = text
        elif ctype == "text/html" and not html:
            html = re.sub(r"<[^>]+>", " ", text)
    return plain or html


def parse_picks(text: str) -> dict | None:
    import html as _html
    text = _html.unescape(text or "")
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not (isinstance(data, dict) and isinstance(data.get("items"), list)):
        return None
    from .page import real_url
    for it in data["items"]:
        if isinstance(it, dict) and it.get("url"):
            it["url"] = real_url(it["url"])        # Gmail wraps links in google.com/url?q=...
    return data


def fetch() -> int:
    user, pwd = os.environ.get("GMAIL_USER"), os.environ.get("GMAIL_APP_PASSWORD")
    if not user or not pwd:
        log.info("Gmail digest: GMAIL_USER / GMAIL_APP_PASSWORD not set, skipping")
        return 0
    seen = read_json(SEEN, [])
    saved = 0
    try:
        imap = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=30)
        imap.login(user, pwd)
        imap.select(f'"{_all_mail(imap)}"', readonly=True)
        since = (now() - dt.timedelta(days=3)).strftime("%d-%b-%Y")
        _, ids = imap.search(None, f'(SUBJECT "{SUBJECT}" SINCE {since})')
        for num in (ids[0] or b"").split():
            _, data = imap.fetch(num, "(RFC822)")
            msg = email.message_from_bytes(data[0][1])
            mid = msg.get("Message-ID", "") or num.decode()
            if mid in seen:
                continue
            seen.append(mid)
            if parseaddr(msg.get("From", ""))[1].lower() != user.lower():
                log.warning("Gmail digest: ignoring picks email not sent from %s", user)
                continue
            subject = str(make_header(decode_header(msg.get("Subject", ""))))
            picks = parse_picks(_body(msg))
            if not picks:
                log.warning("Gmail digest: no picks JSON in '%s'", subject)
                continue
            stamp = re.search(r"(\d{4}-\d{2}-\d{2})\D+(\d{2}):?(\d{2})", subject)
            name = f"{stamp.group(1)}-{stamp.group(2)}{stamp.group(3)}" if stamp else now().strftime("%Y-%m-%d-%H%M")
            DIGEST.mkdir(parents=True, exist_ok=True)
            path = DIGEST / f"{name}.json"
            if path.exists():
                path = DIGEST / f"{name}-{len(seen)}.json"
            write_json(path, picks)
            saved += 1
            log.info("Gmail digest: saved %d pick(s) from '%s' -> %s", len(picks["items"]), subject, path.name)
        imap.logout()
    except (imaplib.IMAP4.error, OSError) as e:
        log.warning("Gmail digest: %s", e)
    write_json(SEEN, seen[-200:])
    return saved
