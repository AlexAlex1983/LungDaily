"""X (Twitter) recent search, API v2. Needs X_BEARER_TOKEN.

Two kinds of query:
  * voices:   (from:a OR from:b ...) -is:retweet           — everything tracked people post
  * keywords: (COPD OR asthma ...) lang:en -is:retweet -is:reply — topic discovery
since_id is stored per query so each run only pays for new posts.
"""
from __future__ import annotations

import os

import requests

from ..util import DATA, iso, load_yaml, log, parse_date, read_json, settings, write_json
from . import make_item

SEARCH_URL = "https://api.x.com/2/tweets/search/recent"
STATE = DATA / "x_state.json"
MAX_QUERY = 500  # stay under 512-char limit


def _chunks(prefix_terms: list[str], suffix: str) -> list[str]:
    queries, cur = [], []
    for t in prefix_terms:
        trial = "(" + " OR ".join(cur + [t]) + ") " + suffix
        if len(trial) > MAX_QUERY and cur:
            queries.append("(" + " OR ".join(cur) + ") " + suffix)
            cur = [t]
        else:
            cur.append(t)
    if cur:
        queries.append("(" + " OR ".join(cur) + ") " + suffix)
    return queries


def build_queries() -> list[tuple[str, str]]:
    voices = [v["x"] for v in load_yaml("voices.yaml").get("voices", []) if v.get("x")]
    keywords = load_yaml("topics.yaml").get("keywords", [])
    out = [("voices", q) for q in _chunks([f"from:{h}" for h in voices], "-is:retweet")]
    out += [("keywords", q) for q in _chunks(keywords, "lang:en -is:retweet -is:reply")]
    return out


def collect() -> list[dict]:
    token = os.environ.get("X_BEARER_TOKEN")
    if not token:
        log.info("X: no X_BEARER_TOKEN, skipping")
        return []
    lim = settings()["limits"]
    state = read_json(STATE, {})
    items: list[dict] = []
    headers = {"Authorization": f"Bearer {token}"}
    for kind, q in build_queries()[: lim["x_max_queries_per_run"]]:
        params = {
            "query": q,
            "max_results": max(10, min(100, lim["x_max_results_per_query"])),
            "tweet.fields": "created_at,public_metrics,author_id,lang,note_tweet,entities",
            "expansions": "author_id",
            "user.fields": "name,username,description,verified,public_metrics",
        }
        if state.get(q):
            params["since_id"] = state[q]
        try:
            r = requests.get(SEARCH_URL, headers=headers, params=params, timeout=30)
            if r.status_code == 402:
                log.error("X: 402 — pay-per-use credits exhausted. Refill credits for the app's account.")
                break
            if r.status_code == 429:
                log.warning("X: rate limited, stopping X collection this run")
                break
            r.raise_for_status()
        except requests.RequestException as e:
            log.warning("X query failed (%s): %s", kind, e)
            continue
        data = r.json()
        users = {u["id"]: u for u in data.get("includes", {}).get("users", [])}
        newest = data.get("meta", {}).get("newest_id")
        if newest:
            state[q] = newest
        for t in data.get("data", []):
            u = users.get(t.get("author_id"), {})
            text = (t.get("note_tweet") or {}).get("text") or t.get("text", "")
            m = t.get("public_metrics", {})
            links = [e.get("expanded_url") for e in (t.get("entities") or {}).get("urls", []) if e.get("expanded_url")]
            items.append(
                make_item(
                    "x",
                    t["id"],
                    url=f"https://x.com/{u.get('username', 'i')}/status/{t['id']}",
                    text=text,
                    author_name=u.get("name", ""),
                    author_handle=u.get("username", ""),
                    author_url=f"https://x.com/{u.get('username')}" if u.get("username") else "",
                    author_headline=u.get("description", ""),
                    published_at=iso(parse_date(t.get("created_at"))),
                    metrics={
                        "likes": m.get("like_count", 0),
                        "reposts": m.get("retweet_count", 0) + m.get("quote_count", 0),
                        "comments": m.get("reply_count", 0),
                    },
                    extra={
                        "tweet_id": t["id"],
                        "query_kind": kind,
                        "links": links,
                        "author_followers": (u.get("public_metrics") or {}).get("followers_count", 0),
                    },
                )
            )
        log.info("X %s query: %d posts", kind, len(data.get("data", [])))
    write_json(STATE, state)
    return items
