"""Picks from the editor's twice-daily health news digest (the scheduled Claude task at 08:00 and
15:00) saved into content/digest/*.json. Only LungDaily topics are used, and only finds whose
article page can be read (same checks as the web-search scout)."""
from __future__ import annotations

from ..util import DATA, ROOT, log, read_json, settings, write_json
from .websearch import to_items

DIGEST = ROOT / "content" / "digest"
SEEN = DATA / "digest_seen.json"


def collect(fetch=None) -> list[dict]:
    from .gmail_digest import fetch as fetch_mail
    fetch_mail()                       # digest emails -> content/digest/*.json
    topics = set((settings().get("web_search") or {}).get("topics") or [])
    seen = read_json(SEEN, [])
    found, new_files = [], []
    for p in sorted(DIGEST.glob("*.json")):
        if p.name in seen:
            continue
        new_files.append(p.name)
        for f in (read_json(p, {}) or {}).get("items", []):
            if not topics or f.get("topic") in topics:
                found.append(f)
    if not new_files:
        return []
    items = to_items(found, fetch=fetch)
    for i in items:
        i["extra"]["feed"] = "Editor digest"
    write_json(SEEN, (seen + new_files)[-200:])
    log.info("Digest: %d file(s), %d LungDaily pick(s), %d with readable pages", len(new_files), len(found), len(items))
    return items
