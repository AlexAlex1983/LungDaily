"""Twice-weekly LinkedIn round-up draft ("Respiratory Health: The Week So Far" / "The Week in Review").

Picks the best stories published since the previous round-up, has Claude write one short bullet per
story from the article's headline and dek ONLY (no new facts), and emails the ready-to-paste post to
the editor inbox for review. Nothing is posted automatically: you check it, tag the organisations
(type @ and pick the page in LinkedIn) and post it yourself.

settings.yaml -> linkedin_digest. Started by .github/workflows/linkedin-digest.yml (Tue + Fri mornings).
Sending uses the same Brevo SMTP setup as the featured emails (SMTP_USER / SMTP_PASSWORD secrets).
"""
from __future__ import annotations

import datetime as dt
import html as _html
import json
import os
import re

from .build import CATEGORY_LABELS
from .util import DATA, POSTS, load_yaml, log, now, parse_date, read_json, settings, write_json

STATE = DATA / "linkedin_digest.json"
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

HASHTAG_WORDS = [   # (pattern in headline/dek/tags, hashtag) — first 5 that match, after the 2 fixed ones
    (r"\bcopd\b", "#COPD"), (r"lung cancer|nsclc|sclc|\blung-cancer\b", "#LungCancer"), (r"\basthma\b", "#Asthma"),
    (r"\bpah\b|pulmonary arterial hypertension|pulmonary hypertension", "#PAH"),
    (r"\bipf\b|pulmonary fibrosis|\bild\b|interstitial", "#IPF"), (r"bronchiectasis", "#Bronchiectasis"),
    (r"tubercul|\btb\b", "#TB"), (r"air pollution|air quality|pm2\.5|particle", "#AirPollution"),
    (r"tobacco|smok|vap|e-cig", "#TobaccoControl"),
]

SYSTEM = """You write a LinkedIn round-up post for LungDaily (respiratory health news).
For each story you get its headline, dek and the organisations named in it. Write ONE bullet per story:
- "label": 1-4 words naming the topic (e.g. "$41bn deal", "PAH guideline", "New ALK option", "COPD").
- "text": one or two plain sentences, present tense, max 40 words, in British English.
- "orgs": organisations to tag, copied EXACTLY as written in the story (companies, societies, agencies);
  max 2; [] if none.
STRICT RULES: use ONLY facts stated in the headline and dek. Add no numbers, superlatives ("biggest",
"first"), context, judgements or claims that are not there. Keep attributions ("according to...",
"a study suggests...") exactly as hedged as the source. Do not put words in anyone's mouth.
Also write "intro": one short neutral sentence opening the post (no hype, no emojis).
Return ONLY JSON: {"intro": "...", "bullets": [{"slug": "...", "label": "...", "text": "...", "orgs": []}]}"""


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 3}


def _numbers(s: str) -> set[str]:
    return set(re.findall(r"\d+(?:[.,]\d+)?", s or ""))


def pick(since: dt.datetime, exclude: set[str], n: int = 8, min_score: float = 7) -> list[dict]:
    recs = []
    takedown = set(load_yaml("optout.yaml").get("takedown") or [])
    for p in POSTS.glob("*.json"):
        r = read_json(p, {})
        d = parse_date(r.get("published_at"))
        if (r.get("status") == "published" and d and since <= d <= now() and r["slug"] not in exclude
                and r["slug"] not in takedown):
            recs.append(r)
    good = [r for r in recs if float(r.get("score") or 0) >= min_score]
    if len(good) >= 5:
        recs = good
    recs.sort(key=lambda r: (-float(r.get("score") or 0), r.get("published_at", "")))
    chosen: list[dict] = []
    for r in recs:
        w = _words(r["headline"])
        # the same story told twice (e.g. two articles on one FDA letter): keep the better one
        if any(len(w & _words(c["headline"])) / max(1, len(w | _words(c["headline"]))) >= 0.3 for c in chosen):
            continue
        chosen.append(r)
        if len(chosen) >= n:
            break
    chosen.sort(key=lambda r: (-float(r.get("score") or 0)))
    return chosen


def _story_text(r: dict) -> str:
    src = (r.get("source") or {}).get("author_name") or ""
    return " ".join([r.get("headline", ""), r.get("dek", ""), r.get("body", ""), src])


def _fallback(r: dict) -> dict:
    return {"slug": r["slug"], "label": CATEGORY_LABELS.get(r["category"], r["category"].title()),
            "text": r["headline"].rstrip(".") + ".", "orgs": []}


def bullets(recs: list[dict]) -> tuple[str, list[dict]]:
    """Claude writes the bullets; each one is checked mechanically against its story and falls back
    to the plain headline if it adds a number, or tags an organisation the story does not name."""
    from .llm import ask_json
    stories = [{"slug": r["slug"], "headline": r["headline"], "dek": r.get("dek", ""),
                "source": (r.get("source") or {}).get("author_name") or ""} for r in recs]
    fake = {"intro": "A look at the main respiratory stories of the past few days.",
            "bullets": [{"slug": r["slug"], "label": CATEGORY_LABELS.get(r["category"], "News"),
                         "text": r["headline"] + ".", "orgs": []} for r in recs]}
    try:
        res = ask_json("writer", SYSTEM, json.dumps(stories, ensure_ascii=False), fake=fake)
    except Exception as e:
        log.warning("LinkedIn digest: writer failed (%s) — using headlines", e)
        res = {}
    by_slug = {b.get("slug"): b for b in (res.get("bullets") or []) if isinstance(b, dict)}
    out = []
    for r in recs:
        b = by_slug.get(r["slug"])
        allowed = " ".join([r["headline"], r.get("dek", "")])
        if (not b or not (b.get("text") or "").strip()
                or not _numbers(b["text"] + " " + (b.get("label") or "")) <= _numbers(allowed)):
            if b:
                log.info("LinkedIn digest: bullet for %s replaced by the headline (new figure)", r["slug"])
            out.append(_fallback(r))
            continue
        story = _story_text(r).lower()
        orgs = [o.strip() for o in (b.get("orgs") or []) if isinstance(o, str) and o.strip()
                and o.strip().lower() in story][:2]
        out.append({"slug": r["slug"], "label": (b.get("label") or "").strip()[:40] or _fallback(r)["label"],
                    "text": b["text"].strip(), "orgs": orgs})
    intro = (res.get("intro") or "").strip() or fake["intro"]
    return intro, out


def hashtags(recs: list[dict]) -> list[str]:
    text = " ".join(f"{r['headline']} {r.get('dek', '')} {' '.join(r.get('tags') or [])}" for r in recs).lower()
    tags = ["#RespiratoryHealth", "#LungHealth"]
    for pat, tag in HASHTAG_WORDS:
        if re.search(pat, text) and tag not in tags:
            tags.append(tag)
    return tags[:7] + (["#Pulmonology"] if len(tags) < 7 else [])


def compose(recs: list[dict], intro: str, items: list[dict], base: str, friday: bool) -> str:
    domain = base.replace("https://", "").replace("http://", "")
    title = "Respiratory Health: The Week in Review 🫁" if friday else "Respiratory Health: The Week So Far 🫁"
    lines = [title, "", intro, ""]
    for b in items:
        orgs = f" ({', '.join('@' + o for o in b['orgs'])})" if b["orgs"] else ""
        lines += [f"🔹 {b['label']}: {b['text']}{orgs}", ""]
    lines.append(f"All stories, sourced and linked: {domain}")
    lines.append(f"This week's newsletter: subscribe at {domain}/newsletter" if friday
                 else f"Full weekly digest lands in your inbox Friday: subscribe at {domain}/newsletter")
    lines += ["", " ".join(hashtags(recs))]
    return "\n".join(lines)


def review_notes(recs: list[dict], base: str) -> list[str]:
    """One line per story with the LungDaily link and the original source, flagging stories that rest
    on a single social-media post from an account that is not one of our voices."""
    voices = load_yaml("voices.yaml").get("voices", [])
    known = {(v.get("x") or "").lower() for v in voices} | {(v.get("name") or "").lower() for v in voices}
    out = []
    for r in recs:
        s = r.get("source") or {}
        flag = ""
        if s.get("source") in ("x", "linkedin", "bluesky") and \
                (s.get("author_handle") or "").lower() not in known and (s.get("author_name") or "").lower() not in known:
            flag = "  ⚠ single social-media post from an account we don't follow — check a news source before posting"
        out.append(f"- {r['headline']}\n  {base}/news/{r['slug']}/\n  source: {s.get('author_name') or '?'} — {s.get('url') or ''}{flag}")
    return out


def run(force: bool = False) -> bool:
    cfg_all = settings()
    cfg = cfg_all.get("linkedin_digest") or {}
    if not cfg.get("enabled"):
        log.info("LinkedIn digest: off (settings.yaml -> linkedin_digest.enabled)")
        return False
    try:
        from zoneinfo import ZoneInfo
        today = now().astimezone(ZoneInfo("Europe/Berlin")).date()
    except Exception:
        today = now().astimezone(dt.timezone(dt.timedelta(hours=1))).date()
    day = WEEKDAYS[today.weekday()]
    if day not in [d.lower()[:3] for d in cfg.get("days") or ["tue", "fri"]] and not force:
        log.info("LinkedIn digest: not a round-up day (%s)", day)
        return False
    state = read_json(STATE, {})
    if state.get("last_date") == today.isoformat() and not force:
        log.info("LinkedIn digest: already made today")
        return False
    last = parse_date(state.get("last_at"))
    since = now() - dt.timedelta(days=int(cfg.get("max_days", 5)))
    if last and last > since:
        since = last - dt.timedelta(hours=2)
    recs = pick(since, set(state.get("last_slugs") or []), n=int(cfg.get("max_items", 8)),
                min_score=float(cfg.get("min_score", 7)))
    if len(recs) < 3:
        log.info("LinkedIn digest: only %d stories since the last round-up, skipped", len(recs))
        return False
    base = cfg_all["site"]["base_url"].rstrip("/")
    intro, items = bullets(recs)
    post = compose(recs, intro, items, base, friday=(day == "fri"))
    orgs = sorted({o for b in items for o in b["orgs"]})
    notes = review_notes(recs, base)
    text = ("LinkedIn round-up draft — check, tag, then post it yourself.\n\n"
            + (f"Tag these pages (type @ and pick the page from LinkedIn's list): {', '.join(orgs)}\n\n" if orgs else "")
            + "=========== COPY FROM HERE ===========\n\n" + post + "\n\n=========== TO HERE ===========\n\n"
            + "Stories and sources:\n" + "\n".join(notes) + "\n\n"
            + "Bullets are written from each article's headline and summary only; check figures against the articles.\n")
    subject = f"LinkedIn draft: {'The Week in Review' if day == 'fri' else 'The Week So Far'} ({today.strftime('%a %-d %b')})"
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"## {subject}\n\n```\n{text}\n```\n")
    from . import featured
    fe = dict(cfg_all.get("featured_email") or {})
    fe["bcc"] = ""
    to = cfg.get("to") or fe.get("from_address", "editor@lungdaily.org")
    sent = False
    if featured.smtp_ready(fe):
        html = ("<pre style='white-space:pre-wrap;font-family:-apple-system,Segoe UI,Arial,sans-serif;font-size:15px'>"
                + _html.escape(text) + "</pre>")
        sent = featured.send(to, subject, text, html, fe)
        log.info("LinkedIn digest emailed to %s: %s", to, sent)
    else:
        log.info("LinkedIn digest: SMTP secrets missing — composed only (see run summary)")
    if sent:
        state.update({"last_date": today.isoformat(), "last_at": now().isoformat(timespec="seconds"),
                      "last_slugs": [r["slug"] for r in recs], "subject": subject, "emailed": sent})
        write_json(STATE, state)
    return sent
