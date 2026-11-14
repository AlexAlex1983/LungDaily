"""Weekly newsletter "The Week in Respiratory Health", sent through Buttondown.

Picks the week's best articles (highest triage score, at least one per section where possible),
writes a Markdown email and creates it in Buttondown via its API.
settings.yaml -> newsletter.mode: "draft" (you press Send in Buttondown) or "send" (goes out at once).
Needs the secret BUTTONDOWN_API_KEY. Started by .github/workflows/newsletter.yml (Fridays).
"""
from __future__ import annotations

import datetime as dt
import os

import requests

from .build import CATEGORY_LABELS
from .util import DATA, POSTS, log, now, parse_date, read_json, settings, write_json

STATE = DATA / "newsletter.json"
API = "https://api.buttondown.com/v1/emails"


def pick(days: int = 7, n: int = 8) -> list[dict]:
    since = now() - dt.timedelta(days=days)
    recs = []
    for p in POSTS.glob("*.json"):
        r = read_json(p, {})
        d = parse_date(r.get("published_at"))
        if r.get("status") == "published" and d and d >= since:
            recs.append(r)
    recs.sort(key=lambda r: (-float(r.get("score") or 0), r.get("published_at", "")))
    chosen, seen_cat, seen_author = [], set(), set()
    for r in recs:                       # first pass: the best story from each section
        a = ((r.get("source") or {}).get("author_name") or "").lower()
        if r["category"] not in seen_cat and a not in seen_author:
            chosen.append(r)
            seen_cat.add(r["category"])
            seen_author.add(a)
    for r in recs:                       # then fill with the next best, max 2 per source
        if len(chosen) >= n:
            break
        a = ((r.get("source") or {}).get("author_name") or "").lower()
        if r not in chosen and sum(1 for c in chosen if ((c.get("source") or {}).get("author_name") or "").lower() == a) < 2:
            chosen.append(r)
    chosen = chosen[:n]
    chosen.sort(key=lambda r: -float(r.get("score") or 0))
    return chosen


def compose(recs: list[dict], site: dict, base: str) -> tuple[str, str]:
    end = now().date()
    start = end - dt.timedelta(days=6)
    rng = f"{start.day} {start.strftime('%B')} – {end.day} {end.strftime('%B %Y')}" if start.month != end.month \
        else f"{start.day}–{end.day} {end.strftime('%B %Y')}"
    subject = f"The Week in Respiratory Health: {recs[0]['headline']}" if recs else "The Week in Respiratory Health"
    lines = [f"*{rng}*", "",
             f"This week's most important stories from {site['name']}, {site['tagline'].lower()}.", ""]
    for i, r in enumerate(recs):
        url = f"{base}/news/{r['slug']}/"
        label = CATEGORY_LABELS.get(r["category"], r["category"].title())
        src = (r.get("source") or {}).get("author_name")
        if i == 0:
            lines += [f"[![]({base}/cards/{r['slug']}.png)]({url})", ""]
        lines += [f"### [{r['headline']}]({url})",
                  f"**{label}**{' · ' + src if src else ''}", "",
                  r.get("dek", ""), ""]
    lines += ["---", "",
              f"Every {site['name']} article links to its original source. Articles are produced with "
              f"AI-assisted tools under our [editorial standards]({base}/editorial-policy/) and checked against "
              f"the source before publication. Not medical advice.", "",
              f"Have news for us? [Send it here]({base}/submit-news/) · Follow "
              f"[@{site.get('x_handle', 'LungDaily')} on X](https://x.com/{site.get('x_handle', 'LungDaily')})"]
    return subject[:150], "\n".join(lines)


def run(force: bool = False) -> bool:
    cfg = settings()
    ncfg, site = cfg.get("newsletter") or {}, cfg["site"]
    base = site["base_url"].rstrip("/")
    if not ncfg.get("enabled"):
        log.info("Newsletter: off (settings.yaml -> newsletter.enabled)")
        return False
    week = now().strftime("%G-W%V")
    state = read_json(STATE, {})
    if state.get("last_week") == week and not force:
        log.info("Newsletter: already created for %s", week)
        return False
    recs = pick(n=int(ncfg.get("top_n", 8)))
    if len(recs) < 3:
        log.info("Newsletter: only %d articles this week, skipping", len(recs))
        return False
    subject, body = compose(recs, site, base)
    key = os.environ.get("BUTTONDOWN_API_KEY")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"## Newsletter {week}\n\n**{subject}**\n\n{body}\n")
    if not key:
        log.info("Newsletter: BUTTONDOWN_API_KEY not set — composed only (see run summary)")
        return False
    status = "about_to_send" if ncfg.get("mode") == "send" else "draft"
    r = requests.post(API, timeout=30, headers={"Authorization": f"Token {key}"},
                      json={"subject": subject, "body": body, "status": status})
    if r.status_code >= 400:
        log.warning("Newsletter: Buttondown refused (%s): %s", r.status_code, r.text[:400])
        return False
    state.update({"last_week": week, "subject": subject, "status": status, "id": r.json().get("id"),
                  "at": now().isoformat(timespec="seconds")})
    write_json(STATE, state)
    log.info("Newsletter %s created in Buttondown as %s: %s", week, status, subject)
    return True
