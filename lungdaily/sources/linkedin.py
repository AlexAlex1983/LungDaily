"""LinkedIn posts via Apify actors (LinkedIn's own API cannot read other people's posts).

* Profile tracking (default): scrapemint/linkedin-profile-posts-scraper — public pages, no login.
  Follows the LinkedIn URLs in config/voices.yaml.
* Keyword search (optional): crawlerbros/linkedin-post-search-scraper — needs a LinkedIn
  `li_at` session cookie (LINKEDIN_LI_AT). Use a separate LinkedIn account, not your own:
  automated use can get the account restricted.

Needs APIFY_TOKEN. Actor IDs can be swapped with APIFY_PROFILE_ACTOR / APIFY_SEARCH_ACTOR;
the normaliser below accepts the common field names used by LinkedIn actors.
"""
from __future__ import annotations

import datetime as dt
import os

import requests

from ..util import iso, load_yaml, log, now, parse_date, settings
from . import make_item

RUN_URL = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
PROFILE_ACTOR = os.environ.get("APIFY_PROFILE_ACTOR", "harvestapi~linkedin-profile-posts")   # HarvestAPI, no cookies, ~$1.50/1k posts (Nov 2026)
SEARCH_ACTOR = os.environ.get("APIFY_SEARCH_ACTOR", "crawlerbros~linkedin-post-search-scraper")


def _run(actor: str, payload: dict) -> list[dict]:
    token = os.environ["APIFY_TOKEN"]
    r = requests.post(RUN_URL.format(actor=actor), params={"token": token}, json=payload, timeout=600)
    r.raise_for_status()
    return r.json()


def _get(d: dict, *keys, default=""):
    for k in keys:
        cur = d
        for part in k.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
            if cur is None:
                break
        if cur not in (None, ""):
            return cur
    return default


def _count(v) -> int:
    if isinstance(v, (list, tuple)):
        return len(v)
    if isinstance(v, dict):
        return sum(_count(x) for x in v.values())
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def normalise(row: dict, via: str) -> dict | None:
    if row.get("noResults") or row.get("kind") in ("reaction", "comment"):
        return None
    if row.get("type") == "repost" or row.get("repost") or row.get("isRepost"):
        return None
    text = _get(row, "text", "content", "postText", "commentary")
    url = _get(row, "url", "postUrl", "linkedinUrl", "shareUrl")
    if not text or not url:
        return None
    pid = str(_get(row, "id", "postId", "urn", default=url))
    urn = _get(row, "urn", "shareUrn", "entityUrn")
    return make_item(
        "linkedin",
        pid.split(":")[-1],
        url=url,
        text=text,
        author_name=_get(row, "author.name", "authorName", "author.fullName"),
        author_url=_get(row, "author.linkedinUrl", "author.url", "authorProfileUrl", "author.profileUrl"),
        author_headline=_get(row, "author.info", "author.headline", "authorHeadline"),
        published_at=iso(parse_date(_get(row, "postedAt.timestamp", "postedAt.date", "postedAt", "postedDate", "date",
                                         "postedAtISO", default=None))),
        metrics={
            "likes": _count(_get(row, "engagement.likes", "engagement.reactions", "reactionsCount", "numLikes", default=0)),
            "reposts": _count(_get(row, "engagement.shares", "engagement.reposts", "repostsCount", "numShares", default=0)),
            "comments": _count(_get(row, "engagement.comments", "commentsCount", "numComments", default=0)),
        },
        extra={"urn": urn, "embed_url": _get(row, "embedUrl"), "via": via},
    )


def collect() -> list[dict]:
    cfg = settings()
    if not os.environ.get("APIFY_TOKEN"):
        log.info("LinkedIn: no APIFY_TOKEN, skipping")
        return []
    lim = cfg["limits"]
    items: list[dict] = []
    since = (now() - dt.timedelta(hours=lim["max_item_age_hours"])).date().isoformat()

    if cfg["sources"].get("linkedin_profiles"):
        # Cost control (Apify pay-per-post): focus voices every write run, everyone else only at
        # sources.linkedin_all_hours (local), and only posts from the last 24 h.
        voices = [v for v in load_yaml("voices.yaml").get("voices", []) if v.get("linkedin")]
        tz = cfg.get("publishing", {}).get("timezone", "Europe/Berlin")
        try:
            from zoneinfo import ZoneInfo
            hour = now().astimezone(ZoneInfo(tz)).hour
        except Exception:
            hour = now().hour
        everyone = hour in [int(h) for h in cfg["sources"].get("linkedin_all_hours", [6, 15])]
        urls = [v["linkedin"] for v in voices if everyone or v.get("focus")]
        log.info("LinkedIn: reading %d profile(s)%s", len(urls), "" if everyone else " (focus voices only)")
        if urls:
            try:
                rows = _run(PROFILE_ACTOR, {
                    "targetUrls": urls,
                    "maxPosts": lim["linkedin_max_posts_per_profile"],
                    "postedLimit": "24h",
                    "includeReposts": False,
                    "includeQuotePosts": True,
                    "scrapeReactions": False,
                    "scrapeComments": False,
                })
                items += [i for i in (normalise(r, "profiles") for r in rows) if i]
            except requests.RequestException as e:
                log.warning("LinkedIn profiles actor failed: %s", e)

    if cfg["sources"].get("linkedin_search"):
        cookie = os.environ.get("LINKEDIN_LI_AT")
        if not cookie:
            log.warning("LinkedIn search enabled but LINKEDIN_LI_AT not set — skipping")
        else:
            for kw in load_yaml("topics.yaml").get("keywords", [])[:6]:
                try:
                    rows = _run(SEARCH_ACTOR, {
                        "searchQuery": kw.replace('"', "").strip("()"),
                        "cookie": cookie,
                        "sortBy": "date_posted",
                        "datePosted": "r86400",
                        "contentType": ["posts", "articles", "documents"],
                        "language": "en",
                        "maxResults": lim["linkedin_search_max_results"],
                        "excludeReposts": True,
                        "minReactions": 10,
                        "emitNoResultsMarker": False,
                    })
                    items += [i for i in (normalise(r, "search") for r in rows) if i]
                except requests.RequestException as e:
                    log.warning("LinkedIn search '%s' failed: %s", kw, e)
    log.info("LinkedIn: %d posts", len(items))
    return items
