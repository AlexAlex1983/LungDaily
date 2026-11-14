"""Respiratory awareness days (config/awareness.yaml).

Around each day the pipeline boosts matching items, adds an extra news/X query, puts the
day's hashtag first on social posts and shows a homepage banner linking to /days/<slug>/.
"""
from __future__ import annotations

import datetime as dt
import re
import urllib.parse

from .util import load_yaml, now

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
          "september", "october", "november", "december"]
ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "last": -1}


def date_for(rule: str, year: int) -> dt.date:
    """'MM-DD' or e.g. 'third wednesday of november' -> date in that year."""
    rule = str(rule).strip().lower()
    m = re.fullmatch(r"(\d{1,2})-(\d{1,2})", rule)
    if m:
        return dt.date(year, int(m.group(1)), int(m.group(2)))
    m = re.fullmatch(r"(first|second|third|fourth|last) (\w+) of (\w+)", rule)
    if not m:
        raise ValueError(f"awareness.yaml: can't read date rule {rule!r}")
    nth, wd, month = ORDINALS[m.group(1)], WEEKDAYS.index(m.group(2)), MONTHS.index(m.group(3)) + 1
    if nth > 0:
        d = dt.date(year, month, 1)
        d += dt.timedelta(days=(wd - d.weekday()) % 7 + 7 * (nth - 1))
        return d
    d = dt.date(year + (month == 12), month % 12 + 1, 1) - dt.timedelta(days=1)
    return d - dt.timedelta(days=(d.weekday() - wd) % 7)


def _config() -> dict:
    try:
        return load_yaml("awareness.yaml") or {}
    except FileNotFoundError:
        return {}


def all_days(year: int | None = None) -> list[dict]:
    cfg = _config()
    year = year or now().year
    out = []
    for d in cfg.get("days") or []:
        rec = dict(d)
        rec["date"] = date_for(d["date"], year)
        rec["days_before"] = int(d.get("days_before", cfg.get("days_before", 3)))
        rec["days_after"] = int(d.get("days_after", cfg.get("days_after", 1)))
        out.append(rec)
    return out


def active(at: dt.datetime | None = None) -> list[dict]:
    """Awareness days whose window includes `at` (default: now). Dates are in Europe/Berlin-ish UTC days."""
    today = (at or now()).date()
    out = []
    for year in (today.year - 1, today.year, today.year + 1):   # windows can cross New Year
        for d in all_days(year):
            if d["date"] - dt.timedelta(days=d["days_before"]) <= today <= d["date"] + dt.timedelta(days=d["days_after"]):
                d["is_today"] = d["date"] == today
                d["days_to_go"] = (d["date"] - today).days
                out.append(d)
    return out


def matches(day: dict, text: str) -> bool:
    text = " " + (text or "") + " "
    for term in day.get("match") or []:
        if term != term.strip():   # padded term (e.g. " TB ") = whole word, case-sensitive
            if re.search(rf"(?<!\w){re.escape(term.strip())}(?!\w)", text):
                return True
        elif term.lower() in text.lower():
            return True
    return False


def item_text(item: dict) -> str:
    return " ".join(str(item.get(k) or "") for k in ("title", "text", "headline", "dek", "body")) + " " + \
        " ".join(item.get("tags") or [])


def days_for(item: dict, at: dt.datetime | None = None) -> list[dict]:
    """Active awareness days this item/article is about."""
    text = item_text(item)
    return [d for d in active(at) if matches(d, text)]


def hashtags_for(rec: dict) -> list[str]:
    return [d["hashtag"] for d in days_for(rec) if d.get("hashtag")]


def rss_feeds() -> list[dict]:
    feeds = []
    for d in active():
        if d.get("news_query"):
            q = urllib.parse.quote_plus(f"({d['news_query']}) when:2d")
            feeds.append({"name": f"Google News — {d['name']}",
                          "url": f"https://news.google.com/rss/search?q={q}&hl=en&gl=US&ceid=US:en"})
    return feeds


def x_queries() -> list[str]:
    """X searches for the day's hashtag (organisations and clinicians post with it)."""
    return [f"{d['hashtag']} lang:en -is:retweet -is:reply" for d in active() if d.get("hashtag")]
