"""Weekly newsletter "The Week in Respiratory Health", sent through Buttondown.

Picks the week's best articles (highest triage score, at least one per section where possible),
writes a Markdown email and creates it in Buttondown via its API.
settings.yaml -> newsletter.mode: "draft" (you press Send in Buttondown) or "send" (goes out at once).
Needs the secret BUTTONDOWN_API_KEY. Started by .github/workflows/newsletter.yml (Fridays).
"""
from __future__ import annotations

import datetime as dt
import json
import os

import requests

from .build import CATEGORY_LABELS
from .util import DATA, POSTS, log, now, parse_date, read_json, settings, write_json

STATE = DATA / "newsletter.json"
API = "https://api.buttondown.com/v1/emails"


def pick(days: int = 7, n: int = 8, min_score: float = 0) -> list[dict]:
    since = now() - dt.timedelta(days=days)
    recs = []
    for p in POSTS.glob("*.json"):
        r = read_json(p, {})
        d = parse_date(r.get("published_at"))
        if r.get("status") == "published" and d and d >= since:
            recs.append(r)
    good = [r for r in recs if float(r.get("score") or 0) >= min_score]
    if len(good) >= 5:   # quality bar: skip a section rather than fill it with a weak story
        recs = good
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
             f"This week's most important stories from {site['name']} — {site['tagline']}.", ""]
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
              f"**Know someone who should read this?** Forward this email — they can subscribe free at "
              f"[{base.replace('https://', '')}/newsletter]({base}/newsletter/?utm_source=newsletter&utm_campaign=forward).", "",
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
    recs = pick(n=int(ncfg.get("top_n", 8)), min_score=float(ncfg.get("min_score", 8)))
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
    langs = [l for l in ncfg.get("languages") or [] if l in LANGUAGES]
    english_filter = _filters([("not_contains", f"lang-{l}") for l in langs]) if langs else None
    email_id = create_email(key, subject, body, status, english_filter)
    if not email_id:
        return False
    state.update({"last_week": week, "subject": subject, "status": status, "id": email_id,
                  "at": now().isoformat(timespec="seconds")})
    editions = {}
    if langs:
        ensure_tags(key, [f"lang-{l}" for l in langs])
    for lang in langs:   # language editions: always drafts, sent only to subscribers who chose that language
        try:
            t_subject, t_body = translate(subject, body, lang)
        except Exception as e:
            log.warning("Newsletter %s edition failed: %s", lang, e)
            continue
        eid = create_email(key, t_subject, t_body, "draft", _filters([("contains", f"lang-{lang}")]))
        if eid:
            editions[lang] = eid
            log.info("Newsletter %s edition created as draft: %s", lang, t_subject)
    state["editions"] = editions
    write_json(STATE, state)
    log.info("Newsletter %s created in Buttondown as %s: %s", week, status, subject)
    return True


# ---------------------------------------------------------------- Buttondown helpers

LANGUAGES = {"es": "Spanish", "fr": "French", "pt": "Portuguese", "ru": "Russian"}
LANG_NOTE = {
    "es": "Traducido del inglés con herramientas de IA. Los artículos enlazados están en inglés.",
    "fr": "Traduit de l'anglais à l'aide d'outils d'IA. Les articles en lien sont en anglais.",
    "pt": "Traduzido do inglês com ferramentas de IA. Os artigos com link estão em inglês.",
    "ru": "Переведено с английского с помощью инструментов ИИ. Статьи по ссылкам — на английском языке.",
}

TRANSLATE_SYSTEM = """You translate the LungDaily newsletter (respiratory health news) from English into {language}.
Rules: translate faithfully and completely, in a clear professional register for health professionals,
patient advocates and policymakers. Keep all Markdown, links (URLs unchanged), image lines, names of
people, organisations, journals, drugs and trials. Use the usual {language} medical terms (e.g. the
local abbreviation for COPD). Text inside quotation marks is a quotation: keep it in the original
language. Add nothing, drop nothing.
Return ONLY JSON: {{"subject": "...", "body": "..."}}"""


def translate(subject: str, body: str, lang: str) -> tuple[str, str]:
    from .llm import ask_json
    language = LANGUAGES[lang]
    res = ask_json("translator", TRANSLATE_SYSTEM.format(language=language),
                   json.dumps({"subject": subject, "body": body}, ensure_ascii=False),
                   fake={"subject": f"[{lang.upper()}] {subject}", "body": body})
    t_subject, t_body = (res.get("subject") or subject).strip(), (res.get("body") or "").strip()
    if not t_body or t_body.count("](") < body.count("](") - 1:   # lost links = broken translation
        raise ValueError("translation dropped links")
    return t_subject[:150], t_body + f"\n\n*{LANG_NOTE[lang]}*"


def _filters(rules: list[tuple[str, str]]) -> dict:
    return {"predicate": "and", "groups": [],
            "filters": [{"operator": op, "field": "subscriber.tags", "value": v} for op, v in rules]}


def create_email(key: str, subject: str, body: str, status: str, filters: dict | None = None) -> str | None:
    payload = {"subject": subject, "body": body, "status": status}
    if filters:
        payload["filters"] = filters
    r = requests.post(API, timeout=30, headers={"Authorization": f"Token {key}"}, json=payload)
    if r.status_code >= 400 and filters:   # older plans/API versions: create without the audience, log it
        log.warning("Newsletter: Buttondown refused the audience filter (%s) — created without it; "
                    "pick the audience in Buttondown before sending", r.status_code)
        payload.pop("filters")
        r = requests.post(API, timeout=30, headers={"Authorization": f"Token {key}"}, json=payload)
    if r.status_code >= 400:
        log.warning("Newsletter: Buttondown refused (%s): %s", r.status_code, r.text[:400])
        return None
    return r.json().get("id") or "ok"


def ensure_tags(key: str, names: list[str]) -> None:
    """Create the language tags in Buttondown if they don't exist yet (best effort)."""
    h = {"Authorization": f"Token {key}"}
    try:
        r = requests.get("https://api.buttondown.com/v1/tags", headers=h, timeout=30)
        have = {t.get("name") for t in (r.json().get("results") or [])} if r.ok else set()
        for n in names:
            if n not in have:
                requests.post("https://api.buttondown.com/v1/tags", headers=h, timeout=30,
                              json={"name": n, "color": "#0B6E73", "description": "Newsletter language edition"})
    except (requests.RequestException, ValueError) as e:
        log.warning("Newsletter: could not check tags: %s", e)


# ---------------------------------------------------------------- congress daily

def congress_daily(force: bool = False) -> bool:
    """On each congress day (config/awareness.yaml -> congresses): a short email with the day's
    congress stories, created as a Buttondown draft (you press Send)."""
    from . import awareness
    cfg = settings()
    ncfg, site = cfg.get("newsletter") or {}, cfg["site"]
    base = site["base_url"].rstrip("/")
    if not ncfg.get("congress_daily", True):
        return False
    live = [d for d in awareness.active() if d.get("until") and d.get("is_today")]
    if not live:
        log.info("Congress daily: no congress on today")
        return False
    state = read_json(STATE, {})
    today = now().date().isoformat()
    made = False
    for day in live:
        k = f"congress:{day['slug']}:{today}"
        if state.get(k) and not force:
            continue
        since = now() - dt.timedelta(hours=26)
        recs = []
        for p in POSTS.glob("*.json"):
            r = read_json(p, {})
            d = parse_date(r.get("published_at"))
            if r.get("status") == "published" and d and d >= since and awareness.matches(day, awareness.item_text(r)):
                recs.append(r)
        recs.sort(key=lambda r: -float(r.get("score") or 0))
        if len(recs) < 2:
            log.info("Congress daily %s: only %d stories today, skipped", day["name"], len(recs))
            continue
        n = (now().date() - day["date"]).days + 1
        subject = f"{day['name']} Daily, day {n}: {recs[0]['headline']}"[:150]
        lines = [f"*{day['name']}{', ' + day['city'] if day.get('city') else ''} · day {n} · {day.get('hashtag', '')}*", "",
                 f"Today's {day['name']} stories from {site['name']}. All coverage: [{base.replace('https://', '')}/days/{day['slug']}]({base}/days/{day['slug']}/)", ""]
        for i, r in enumerate(recs[:8]):
            url = f"{base}/news/{r['slug']}/"
            if i == 0:
                lines += [f"[![]({base}/cards/{r['slug']}.png)]({url})", ""]
            src = (r.get("source") or {}).get("author_name")
            lines += [f"### [{r['headline']}]({url})", f"{r.get('dek', '')}{' — ' + src if src else ''}", ""]
        lines += ["---", "", f"Based on public abstracts, press releases and posts; every article links its source. "
                  f"Produced with AI-assisted tools under our [editorial standards]({base}/editorial-policy/). Not medical advice."]
        body = "\n".join(lines)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as f:
                f.write(f"## {subject}\n\n{body}\n")
        key = os.environ.get("BUTTONDOWN_API_KEY")
        if not key:
            log.info("Congress daily: BUTTONDOWN_API_KEY not set — composed only")
            continue
        if create_email(key, subject, body, "draft"):
            state[k] = now().isoformat(timespec="seconds")
            made = True
            log.info("Congress daily draft created: %s", subject)
    write_json(STATE, state)
    return made
