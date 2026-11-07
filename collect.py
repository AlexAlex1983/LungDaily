"""Collect from all enabled sources -> dedupe, filter opt-outs and old items -> data/inbox.json."""
from __future__ import annotations

import datetime as dt

from .sources import linkedin, pubmed, rss, x
from .util import DATA, load_yaml, log, now, parse_date, read_json, settings, write_json

INBOX = DATA / "inbox.json"
SEEN = DATA / "seen.json"


def _opted_out(item: dict, opt: dict) -> bool:
    handles = {h.lower().lstrip("@") for h in opt.get("x_handles") or []}
    urls = {u.lower().rstrip("/") for u in opt.get("linkedin_urls") or []}
    names = {n.lower() for n in opt.get("names") or []}
    return (
        (item.get("author_handle") or "").lower() in handles
        or (item.get("author_url") or "").lower().rstrip("/") in urls
        or (item.get("author_name") or "").lower() in names
    )


def add_items(new: list[dict]) -> int:
    cfg = settings()
    opt = load_yaml("optout.yaml")
    seen: dict = read_json(SEEN, {})
    inbox: list = read_json(INBOX, [])
    seen_urls = {i["url"] for i in inbox}
    cutoff = now() - dt.timedelta(hours=cfg["limits"]["max_item_age_hours"])
    added = 0
    for it in new:
        if it["id"] in seen or it["url"] in seen_urls or _opted_out(it, opt):
            continue
        pub = parse_date(it.get("published_at"))
        if pub and pub < cutoff and it["source"] != "pubmed":
            continue
        it["collected_at"] = now().isoformat(timespec="seconds")
        inbox.append(it)
        seen[it["id"]] = it["collected_at"]
        seen_urls.add(it["url"])
        added += 1
    # forget ids older than 30 days to keep the file small
    old = (now() - dt.timedelta(days=30)).isoformat()
    seen = {k: v for k, v in seen.items() if v >= old}
    write_json(SEEN, seen)
    write_json(INBOX, inbox)
    return added


def run() -> int:
    enabled = settings()["sources"]
    collected: list[dict] = []
    if enabled.get("x"):
        collected += x.collect()
    if enabled.get("linkedin_profiles") or enabled.get("linkedin_search"):
        collected += linkedin.collect()
    if enabled.get("pubmed"):
        collected += pubmed.collect()
    if enabled.get("rss"):
        collected += rss.collect()
    added = add_items(collected)
    log.info("Collected %d, new in inbox: %d", len(collected), added)
    return added
