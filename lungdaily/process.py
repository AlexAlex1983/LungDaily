"""Triage -> write -> fact-check -> publish or hold.

Every collected item is scored by a cheap model. The best are written up by the writer model,
then an independent checker compares the article with the source. Articles that pass are
published automatically; the rest go to data/held.json for one-click release.
"""
from __future__ import annotations

import datetime as dt
import json

from .llm import LLMError, ask_json
from .util import (DATA, POSTS, load_yaml, log, norm_text, now, parse_date, read_json,
                   settings, slugify, word_count, write_json)

INBOX = DATA / "inbox.json"
HELD = DATA / "held.json"
SUGGESTED = DATA / "suggested_voices.json"
AMPLIFY_QUEUE = DATA / "amplify_queue.json"
SLOTS_STATE = DATA / "slots.json"

# ---------------------------------------------------------------- prompts

TRIAGE_SYSTEM = """You are the news editor of LungDaily, a daily news site for the respiratory
community (COPD, asthma, lung cancer and screening, bronchiectasis, ILD/pulmonary fibrosis, TB,
pulmonary rehab, air pollution, tobacco and vaping, AI in lung health, respiratory policy).

Score each item 0-10 for whether it deserves its own short news article:
 9-10  major: new guidelines (GOLD, GINA, ERS/ATS), WHO/UN or national policy decisions, landmark
       trials or meta-analyses in top journals, regulatory approvals, big funding or appointments
 7-8   solid: a substantive post by a recognised expert or organisation sharing data, a result,
       a policy view or an event; a notable new study; meaningful air-quality/tobacco policy news
 4-6   minor: generic awareness messages, vague opinions, reposts of old news, conference selfies
 0-3   off-topic, promotional/sales, personal chatter, political attacks, misinformation,
       content about identifiable patients, requests for medical advice

Also assign one category: voices | research | policy | approvals | people | events.
Use "voices" for social posts where the person's own view or work is the story.
Use "approvals" for regulatory decisions (FDA, EMA/CHMP, MHRA, NMPA, PMDA, WHO prequalification),
label expansions, new devices or drugs reaching patients, and phase 2/3 trial readouts (topline
results, trial starts or stops) in respiratory disease — including from company press releases,
but score marketing-only items low.
Use "people" for appointments, elections (society presidents, editors, chairs), awards and honours,
retirements and obituaries of people in the respiratory field.
Also give author_type: "person" (an individual clinician, researcher, advocate or patient),
"organisation" (society, journal, agency, company, charity) or "media" (news outlet).
If an item repeats a story already in RECENT HEADLINES, set duplicate=true.

Return ONLY JSON: [{"id": "...", "score": 0-10, "category": "...", "author_type": "person|organisation|media", "duplicate": false, "reason": "<12 words"}]"""

WRITER_SYSTEM = """You write short news articles for LungDaily from a single source
(a social media post, a paper abstract, or a news/RSS item).

Hard rules:
- Use ONLY facts stated in the source. Never add numbers, dates, affiliations, or background
  that the source does not contain. If something is unclear, leave it out.
- Attribute: "X said", "X wrote on LinkedIn", "according to the study". Do not present a
  person's opinion as fact.
- Neutral, precise, professional tone. No hype ("groundbreaking", "game-changer").
- No medical advice and no recommendations to patients.
- British English.

Format by category:
- voices / people: headline "<Full Name>: <what they said or did>"  (max 110 chars)
- research: headline states the main finding plainly; mention the journal in the body
- other categories: plain factual headline (max 110 chars)
- dek: one sentence, max 30 words
- body: Markdown, 2-4 short paragraphs, 110-280 words
- key_quote: an EXACT verbatim excerpt from the source text (max 25 words), or "" if none fits
- tags: 2-5 lowercase tags from: copd, asthma, lung-cancer, screening, bronchiectasis, ild,
  tb, rehab, air-pollution, tobacco, vaping, ai, policy, who, guidelines, trial, paediatrics,
  primary-care, global-health, ncds, equity, diagnostics, therapeutics
- people: names of people named in the source

Return ONLY JSON: {"headline": "", "dek": "", "body": "", "key_quote": "", "tags": [], "people": []}"""

CHECKER_SYSTEM = """You are the fact-checker of LungDaily. Compare the ARTICLE with the SOURCE.
Fail the article if ANY of these is true:
- a fact, number, name, date or affiliation is not supported by the source
- a quote is not verbatim from the source, or words are attributed to the wrong person
- an opinion is presented as established fact, or the headline overstates the source
- it gives medical advice, or could be defamatory, or reveals details about an identifiable patient
Minor style issues are not failures.

Return ONLY JSON: {"pass": true|false, "issues": ["..."]}"""


# ---------------------------------------------------------------- helpers

def _voice_tiers() -> dict:
    tiers = {}
    for v in load_yaml("voices.yaml").get("voices", []):
        if v.get("x"):
            tiers[v["x"].lower()] = v.get("tier", 2)
        if v.get("linkedin"):
            tiers[v["linkedin"].lower().rstrip("/")] = v.get("tier", 2)
    return tiers


def _brief(item: dict, tiers: dict) -> dict:
    tier = tiers.get((item.get("author_handle") or "").lower()) or tiers.get(
        (item.get("author_url") or "").lower().rstrip("/"))
    return {
        "id": item["id"],
        "source": item["source"],
        "author": item.get("author_name"),
        "author_bio": (item.get("author_headline") or "")[:160],
        "tracked_voice_tier": tier,
        "title": item.get("title", "")[:200],
        "text": (item.get("text") or "")[:900],
        "journal": item.get("extra", {}).get("journal"),
        "engagement": item.get("metrics"),
        "published_at": item.get("published_at"),
    }


def _all_posts() -> list[dict]:
    return [read_json(p, {}) for p in sorted(POSTS.glob("*.json"))]


def recent_headlines(days: int = 4) -> list[str]:
    cutoff = now() - dt.timedelta(days=days)
    out = []
    for p in _all_posts() + read_json(HELD, []):
        d = parse_date(p.get("published_at") or p.get("created_at"))
        if d and d >= cutoff:
            out.append(p["headline"])
    return out


def published_today() -> int:
    today = now().date()
    return sum(1 for p in _all_posts() if p.get("status") == "published"
               and (parse_date(p.get("published_at")) or now()).date() == today)


def written_today() -> int:
    """Articles written today (published or waiting in the ready queue)."""
    today = now().date()
    return sum(1 for p in _all_posts() if p.get("status") in ("published", "ready")
               and (parse_date(p.get("created_at")) or now()).date() == today)


def ready_posts() -> list[dict]:
    return [p for p in _all_posts() if p.get("status") == "ready"]


def save_ready(rec: dict) -> None:
    """Article passed the checks; it waits for the next publishing slot."""
    rec["status"] = "ready"
    write_json(POSTS / f"{rec['slug']}.json", rec)


def _source_text(item: dict) -> str:
    parts = []
    if item.get("title"):
        parts.append(f"TITLE: {item['title']}")
    parts.append(f"TEXT: {item.get('text', '')}")
    return "\n".join(parts)


def _source_block(item: dict) -> str:
    meta = {
        "source_type": {"x": "post on X", "linkedin": "LinkedIn post", "pubmed": "paper abstract (PubMed)",
                        "rss": "news item"}[item["source"]],
        "author": item.get("author_name"),
        "author_bio": item.get("author_headline"),
        "journal": item.get("extra", {}).get("journal"),
        "published_at": item.get("published_at"),
        "url": item.get("url"),
    }
    return "SOURCE METADATA:\n" + json.dumps(meta, ensure_ascii=False) + "\n\n" + _source_text(item)


# ---------------------------------------------------------------- steps

def triage(items: list[dict]) -> None:
    """Adds item['triage'] = {score, category, duplicate, reason} in place (only for untriaged)."""
    todo = [i for i in items if "triage" not in i]
    if not todo:
        return
    tiers = _voice_tiers()
    heads = recent_headlines()
    size = settings()["limits"]["triage_batch_size"]
    for start in range(0, len(todo), size):
        batch = todo[start : start + size]
        user = ("RECENT HEADLINES:\n" + "\n".join(f"- {h}" for h in heads[-60:]) + "\n\nITEMS:\n"
                + json.dumps([_brief(i, tiers) for i in batch], ensure_ascii=False))

        def fake(batch=batch):
            return [{"id": i["id"], "score": 8, "category": "research" if i["source"] == "pubmed" else "voices",
                     "duplicate": False, "reason": "test"} for i in batch]
        try:
            res = ask_json("triage", TRIAGE_SYSTEM, user, fake=fake)
        except LLMError as e:
            log.error("Triage failed: %s", e)
            continue
        by_id = {r.get("id"): r for r in res if isinstance(r, dict)}
        for i in batch:
            r = by_id.get(i["id"])
            if r:
                i["triage"] = {"score": float(r.get("score", 0)), "category": r.get("category", "voices"),
                               "author_type": r.get("author_type", ""),
                               "duplicate": bool(r.get("duplicate")), "reason": r.get("reason", "")}


def write(item: dict) -> dict:
    cat = item["triage"]["category"]
    user = f"CATEGORY: {cat}\n\n{_source_block(item)}"

    def fake():
        words = (item.get("text") or "").split()
        return {"headline": f"{item.get('author_name') or 'Study'}: {' '.join(words[:8])}",
                "dek": "Test dek for offline run.",
                "body": ("Offline test body paragraph. " * 25).strip(),
                "key_quote": " ".join(words[:10]),
                "tags": ["copd", "test"], "people": [item.get("author_name")]}
    return ask_json("writer", WRITER_SYSTEM, user, fake=fake)


def check(item: dict, art: dict) -> dict:
    cfg = settings()["publishing"]
    issues = []
    if art.get("key_quote") and norm_text(art["key_quote"]) not in norm_text(_source_text(item)):
        issues.append("key_quote is not verbatim from source")
    wc = word_count(art.get("body", ""))
    if not cfg["min_body_words"] <= wc <= cfg["max_body_words"]:
        issues.append(f"body length {wc} words outside limits")
    if len(art.get("headline", "")) > 130 or not art.get("headline"):
        issues.append("headline missing or too long")
    user = f"{_source_block(item)}\n\nARTICLE:\n{json.dumps(art, ensure_ascii=False)}"
    try:
        verdict = ask_json("checker", CHECKER_SYSTEM, user, fake={"pass": True, "issues": []})
    except LLMError as e:
        verdict = {"pass": False, "issues": [f"checker unavailable: {e}"]}
    issues += verdict.get("issues", []) if not verdict.get("pass") else []
    return {"pass": bool(verdict.get("pass")) and not issues, "issues": issues}


def source_handles(item: dict) -> list[str]:
    """X handles that appear verbatim in an X post (people/orgs the author tagged)."""
    if item["source"] != "x":
        return []
    import re
    seen, out = set(), []
    for h in re.findall(r"(?<![\w@])@(\w{1,15})", item.get("text") or ""):
        if h.lower() not in seen:
            seen.add(h.lower())
            out.append(h)
    return out


def _embed(item: dict) -> dict:
    if item["source"] == "x":
        return {"type": "x", "tweet_id": item["extra"].get("tweet_id"), "url": item["url"]}
    if item["source"] == "linkedin":
        urn = item["extra"].get("urn") or ""
        embed = item["extra"].get("embed_url") or (
            f"https://www.linkedin.com/embed/feed/update/{urn}" if urn.startswith("urn:li:") else "")
        return {"type": "linkedin", "embed_url": embed, "url": item["url"]}
    return {"type": "link", "url": item["url"]}


def _unique_slug(base: str) -> str:
    existing = {p.stem for p in POSTS.glob("*.json")} | {h["slug"] for h in read_json(HELD, [])}
    slug, n = base, 2
    while slug in existing:
        slug, n = f"{base}-{n}", n + 1
    return slug


def publish_record(rec: dict) -> None:
    rec["status"] = "published"
    rec["published_at"] = now().isoformat(timespec="seconds")
    write_json(POSTS / f"{rec['slug']}.json", rec)
    q = read_json(AMPLIFY_QUEUE, [])
    q.append(rec["slug"])
    write_json(AMPLIFY_QUEUE, q)


def author_key(item: dict) -> str:
    return ((item.get("author_handle") or item.get("author_name") or "").strip().lower()
            or (item.get("source") or "") + ":" + (item.get("extra", {}).get("feed") or ""))


def rank(item: dict, sel: dict) -> float:
    """Triage score plus small boosts: under-served sections, individual voices."""
    t = item["triage"]
    boost = float((sel.get("category_boost") or {}).get(t["category"], 0))
    if t.get("author_type") == "person":
        boost += float(sel.get("person_boost", 0))
    return t["score"] + boost


def authors_today() -> dict:
    today, out = now().date(), {}
    for p in _all_posts():
        if p.get("status") not in ("published", "ready"):
            continue
        if (parse_date(p.get("published_at") or p.get("created_at")) or now()).date() == today:
            k = author_key(p.get("source") or {})
            out[k] = out.get(k, 0) + 1
    return out


def pick(candidates: list[dict], room: int, sel: dict) -> tuple[list[dict], list[dict]]:
    """Best-ranked items, but at most N per author per run / per day, so one prolific
    account (e.g. a journal) cannot fill the site."""
    per_run = int(sel.get("max_per_author_per_run", 1))
    per_day = int(sel.get("max_per_author_per_day", 2))
    counts = authors_today()
    run_counts: dict = {}
    chosen, rest = [], []
    for i in candidates:
        k = author_key(i)
        if len(chosen) < room and run_counts.get(k, 0) < per_run and counts.get(k, 0) + run_counts.get(k, 0) < per_day:
            chosen.append(i)
            run_counts[k] = run_counts.get(k, 0) + 1
        else:
            rest.append(i)
    return chosen, rest


def run() -> dict:
    cfg = settings()
    lim, pub = cfg["limits"], cfg["publishing"]
    inbox: list = read_json(INBOX, [])
    triage(inbox)

    note_suggestions(inbox)
    cutoff = now() - dt.timedelta(hours=lim["max_item_age_hours"])
    sel = cfg.get("selection") or {}
    min_by_cat = sel.get("min_score_by_category") or {}
    keep, candidates = [], []
    for i in inbox:
        t = i.get("triage")
        if t is None:  # triage failed — retry next run
            keep.append(i)
        elif t["score"] >= float(min_by_cat.get(t["category"], lim["min_score"])) and not t["duplicate"]:
            candidates.append(i)
    candidates.sort(key=lambda i: (rank(i, sel), sum(i["metrics"].values())), reverse=True)

    if pub.get("slots"):   # scheduled mode: keep a small buffer of ready articles for the slots
        room = min(lim["max_articles_per_run"],
                   max(0, int(pub.get("ready_buffer", 6)) - len(ready_posts())),
                   max(0, lim["max_articles_per_day"] - written_today()))
    else:
        room = min(lim["max_articles_per_run"], max(0, lim["max_articles_per_day"] - published_today()))
    to_write, leftover = pick(candidates, room, sel)
    # good items over the cap wait for the next run unless too old
    keep += [i for i in leftover if (parse_date(i.get("collected_at")) or now()) >= cutoff]

    held = read_json(HELD, [])
    stats = {"triaged": len(inbox), "candidates": len(candidates), "published": 0, "held": 0, "failed": 0}
    for item in to_write:
        try:
            art = write(item)
        except LLMError as e:
            log.error("Writer failed for %s: %s", item["id"], e)
            stats["failed"] += 1
            keep.append(item)
            continue
        verdict = check(item, art)
        date = now().date().isoformat()
        rec = {
            "slug": _unique_slug(f"{date}-{slugify(art['headline'])}"),
            "headline": art["headline"].strip(),
            "dek": art.get("dek", "").strip(),
            "body": art.get("body", "").strip(),
            "key_quote": art.get("key_quote", "").strip(),
            "tags": [t.lower() for t in art.get("tags", [])][:5],
            "people": [p for p in art.get("people", []) if p],
            "category": item["triage"]["category"],
            "score": item["triage"]["score"],
            "created_at": now().isoformat(timespec="seconds"),
            "checker": verdict,
            "embed": _embed(item),
            "mentions": source_handles(item),
            "source": {k: item.get(k) for k in ("id", "source", "url", "title", "author_name", "author_handle",
                                                 "author_url", "published_at", "extra")},
        }
        if pub["auto_publish"] and (verdict["pass"] or not pub["require_checker_pass"]):
            if pub.get("slots"):
                save_ready(rec)
                stats["ready"] = stats.get("ready", 0) + 1
                log.info("Ready for next slot: %s", rec["headline"])
            else:
                publish_record(rec)
                stats["published"] += 1
                log.info("Published: %s", rec["headline"])
        else:
            rec["status"] = "held"
            held.append(rec)
            stats["held"] += 1
            log.info("Held: %s — %s", rec["headline"], "; ".join(verdict["issues"]) or "auto_publish off")
    write_json(HELD, held)
    write_json(INBOX, keep)
    log.info("Process stats: %s", stats)
    return stats


def note_suggestions(items: list[dict]) -> None:
    """Remember X authors (not yet followed) whose keyword-search posts scored >= 8."""
    known = {v.get("x", "").lower() for v in load_yaml("voices.yaml").get("voices", []) if v.get("x")}
    sug = read_json(SUGGESTED, {})
    for i in items:
        t = i.get("triage") or {}
        h = (i.get("author_handle") or "").lower()
        if i["source"] != "x" or not h or h in known or t.get("score", 0) < 8:
            continue
        e = sug.setdefault(h, {"handle": i["author_handle"], "name": i.get("author_name"),
                               "bio": (i.get("author_headline") or "")[:160], "hits": 0,
                               "followers": i.get("extra", {}).get("author_followers", 0), "example": i["url"]})
        e["hits"] += 1
        e["last_seen"] = now().isoformat(timespec="seconds")
    write_json(SUGGESTED, sug)


def _local_now(tz: str) -> dt.datetime:
    from zoneinfo import ZoneInfo
    return now().astimezone(ZoneInfo(tz))


def publish_slot() -> str | None:
    """Publish ONE ready article if a publishing slot is due (settings: publishing.slots, local time).
    Catch-up rule: if fewer slot articles went out today than slots have passed, publish one.
    So a late or skipped GitHub run is made up at the next run, but never more than one per run."""
    pub = settings()["publishing"]
    slots = sorted(pub.get("slots") or [])
    if not slots:
        return None
    local = _local_now(pub.get("timezone", "Europe/Berlin"))
    hhmm = local.strftime("%H:%M")
    due = sum(1 for s in slots if s <= hhmm)
    state = read_json(SLOTS_STATE, {})
    today = local.date().isoformat()
    done = state.get("count", 0) if state.get("date") == today else 0
    # drop ready articles that waited too long
    max_age = float(pub.get("ready_max_age_hours", 36))
    ready = []
    for r in ready_posts():
        made = parse_date(r.get("created_at")) or now()
        if (now() - made).total_seconds() > max_age * 3600:
            r["status"] = "expired"
            write_json(POSTS / f"{r['slug']}.json", r)
            log.info("Expired (not published in time): %s", r["headline"])
        else:
            ready.append(r)
    if done >= due:
        log.info("Slot: nothing due at %s (%d of %d slots used today, %d ready)", hhmm, done, due, len(ready))
        return None
    if not ready:
        log.info("Slot due at %s but no ready article", hhmm)
        return None
    # best first; at equal score the older one; avoid the same author twice in a row
    ready.sort(key=lambda r: (-float(r.get("score") or 0), r.get("created_at") or ""))
    last = sorted((p for p in _all_posts() if p.get("status") == "published"),
                  key=lambda p: p.get("published_at") or "")
    last_author = author_key(last[-1].get("source") or {}) if last else None
    rec = next((r for r in ready if author_key(r.get("source") or {}) != last_author), ready[0])
    # slug carries the publication date
    old = rec["slug"]
    date = now().date().isoformat()
    if not old.startswith(date):
        (POSTS / f"{old}.json").unlink(missing_ok=True)
        rec["slug"] = _unique_slug(f"{date}-{old[11:]}")
    publish_record(rec)
    write_json(SLOTS_STATE, {"date": today, "count": done + 1})
    log.info("Slot %d/%d published: %s", done + 1, len(slots), rec["headline"])
    return rec["slug"]


def release(slug: str) -> bool:
    """Publish a held article (after you reviewed or fixed it)."""
    held = read_json(HELD, [])
    for rec in held:
        if rec["slug"] == slug:
            held.remove(rec)
            publish_record(rec)
            write_json(HELD, held)
            return True
    return False


def discard(slug: str) -> bool:
    held = read_json(HELD, [])
    new = [r for r in held if r["slug"] != slug]
    write_json(HELD, new)
    return len(new) != len(held)
