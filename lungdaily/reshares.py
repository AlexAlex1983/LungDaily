"""Who shares LungDaily — the "featured" sharing loop, measured.

data/reshares.json:
  featured: {slug: {"org": ..., "to": ..., "at": ...}}           every "you've been featured" note sent
  shares:   [{"slug", "by", "handle", "channel", "url", "at", "featured"}]   public posts that link a LungDaily article

Sources of shares:
  * X: one search a day for posts linking lungdaily.org (not by @LungDaily itself), via the X API.
  * LinkedIn: the voices' posts we read through Apify anyway — any that link or name LungDaily.
  * Website visits from the share kit carry utm_campaign=featured (see GoatCounter -> Campaigns).
"""
from __future__ import annotations

import datetime as dt
import os
import re

import requests

from .util import DATA, load_yaml, log, now, parse_date, read_json, settings, write_json

STATE = DATA / "reshares.json"
SEARCH_URL = "https://api.x.com/2/tweets/search/recent"
SLUG_RE = re.compile(r"lungdaily\.(?:org|com)/news/([a-z0-9-]+)", re.I)


def _state() -> dict:
    s = read_json(STATE, {})
    s.setdefault("featured", {})
    s.setdefault("shares", [])
    return s


def note_featured(slug: str, org: str, to: str) -> None:
    s = _state()
    s["featured"][slug] = {"org": org, "to": to, "at": now().isoformat(timespec="seconds")}
    write_json(STATE, s)


def _known(s: dict, url: str) -> bool:
    return any(x.get("url") == url for x in s["shares"])


def record(slug: str, by: str, handle: str, channel: str, url: str, at: str | None = None) -> bool:
    s = _state()
    if not url or _known(s, url):
        return False
    s["shares"].append({"slug": slug, "by": by, "handle": handle, "channel": channel, "url": url,
                        "at": at or now().isoformat(timespec="seconds"), "featured": slug in s["featured"]})
    write_json(STATE, s)
    log.info("Reshare: %s shared %s on %s", by or handle, slug or "LungDaily", channel)
    return True


def slugs_in(text: str) -> list[str]:
    return list(dict.fromkeys(m.group(1).lower() for m in SLUG_RE.finditer(text or "")))


def from_linkedin_rows(rows: list[dict]) -> int:
    """Called with the raw Apify rows (before reposts are dropped): note posts that link/name LungDaily."""
    n = 0
    for r in rows:
        blob = " ".join(str(r.get(k) or "") for k in ("content", "text", "linkedinUrl", "url", "article", "header"))
        if "lungdaily" not in blob.lower():
            continue
        author = r.get("author") or {}
        name = author.get("name") if isinstance(author, dict) else str(author)
        slugs = slugs_in(blob) or [""]
        url = r.get("linkedinUrl") or r.get("url") or ""
        for sl in slugs:
            n += record(sl, name or "", "", "linkedin", url + ("#" + sl if len(slugs) > 1 else ""))
    return n


def check_x(force: bool = False) -> int:
    """Once a day: X posts linking lungdaily.org by anyone but @LungDaily."""
    token = (os.environ.get("X_BEARER_TOKEN") or "").strip()
    if not token:
        return 0
    s = _state()
    last = parse_date(s.get("x_checked_at"))
    if last and now() - last < dt.timedelta(hours=22) and not force:
        return 0
    own = (settings()["site"].get("x_handle") or "LungDaily")
    params = {"query": f'url:"lungdaily.org" -from:{own} -is:retweet', "max_results": 50,
              "tweet.fields": "created_at,entities,author_id", "expansions": "author_id", "user.fields": "name,username"}
    if s.get("x_since_id"):
        params["since_id"] = s["x_since_id"]
    try:
        r = requests.get(SEARCH_URL, headers={"Authorization": f"Bearer {token}"}, params=params, timeout=30)
        if r.status_code >= 400:
            log.warning("Reshares: X search %s %s", r.status_code, r.text[:200])
            return 0
    except requests.RequestException as e:
        log.warning("Reshares: X search failed: %s", e)
        return 0
    data = r.json()
    s = _state()
    s["x_checked_at"] = now().isoformat(timespec="seconds")
    if data.get("meta", {}).get("newest_id"):
        s["x_since_id"] = data["meta"]["newest_id"]
    write_json(STATE, s)
    users = {u["id"]: u for u in data.get("includes", {}).get("users", [])}
    n = 0
    for t in data.get("data", []):
        u = users.get(t.get("author_id"), {})
        links = " ".join(e.get("expanded_url") or "" for e in (t.get("entities") or {}).get("urls", []))
        url = f"https://x.com/{u.get('username', 'i')}/status/{t['id']}"
        for sl in slugs_in(links + " " + (t.get("text") or "")) or [""]:
            n += record(sl, u.get("name", ""), u.get("username", ""), "x", url + (f"#{sl}" if sl else ""),
                        t.get("created_at"))
    log.info("Reshares: X search found %d new share(s)", n)
    return n


def report(days: int = 30) -> dict:
    """{'featured': N, 'reshared': M, 'rate': %, 'shares': K, 'by_channel': {...}, 'top': [(name, n)]}."""
    s = _state()
    since = now() - dt.timedelta(days=days)
    feat = {k: v for k, v in s["featured"].items() if (parse_date(v.get("at")) or now()) >= since}
    shares = [x for x in s["shares"] if (parse_date(x.get("at")) or now()) >= since]
    reshared = {x["slug"] for x in shares if x.get("slug") in feat}
    by_channel: dict = {}
    who: dict = {}
    for x in shares:
        by_channel[x["channel"]] = by_channel.get(x["channel"], 0) + 1
        k = x.get("by") or x.get("handle") or "?"
        who[k] = who.get(k, 0) + 1
    return {"days": days, "featured": len(feat), "reshared": len(reshared),
            "rate": round(100 * len(reshared) / len(feat)) if feat else 0, "shares": len(shares),
            "by_channel": by_channel, "top": sorted(who.items(), key=lambda kv: -kv[1])[:10]}


def summary_lines(days: int = 30) -> list[str]:
    r = report(days)
    lines = ["", f"### Sharing loop (last {days} days)", "",
             f"- Featured notes sent: **{r['featured']}** · articles reshared by the featured organisation or anyone: "
             f"**{r['reshared']}** ({r['rate']}%) · all public shares found: **{r['shares']}** "
             + (f"({', '.join(f'{k} {v}' for k, v in r['by_channel'].items())})" if r["by_channel"] else ""),
             "- Visits from the share kit: GoatCounter → Campaigns → `featured`"]
    if r["top"]:
        lines.append("- Top sharers: " + ", ".join(f"{k} ({v})" for k, v in r["top"]))
    return lines


def write_summary(days: int = 30) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(summary_lines(days)) + "\n")
