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
AMPLIFY_QUEUE = DATA / "amplify_queue.json"

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
If an item repeats a story already in RECENT HEADLINES, set duplicate=true.

Return ONLY JSON: [{"id": "...", "score": 0-10, "category": "...", "duplicate": false, "reason": "<12 words"}]"""

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
    return sum(1 for p in _all_posts() if (parse_date(p.get("published_at")) or now()).date() == today)


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


def run() -> dict:
    cfg = settings()
    lim, pub = cfg["limits"], cfg["publishing"]
    inbox: list = read_json(INBOX, [])
    triage(inbox)

    cutoff = now() - dt.timedelta(hours=lim["max_item_age_hours"])
    keep, candidates = [], []
    for i in inbox:
        t = i.get("triage")
        if t is None:  # triage failed — retry next run
            keep.append(i)
        elif t["score"] >= lim["min_score"] and not t["duplicate"]:
            candidates.append(i)
    candidates.sort(key=lambda i: (i["triage"]["score"], sum(i["metrics"].values())), reverse=True)

    room = min(lim["max_articles_per_run"], max(0, lim["max_articles_per_day"] - published_today()))
    to_write, leftover = candidates[:room], candidates[room:]
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
            "source": {k: item.get(k) for k in ("id", "source", "url", "title", "author_name", "author_handle",
                                                 "author_url", "published_at", "extra")},
        }
        if pub["auto_publish"] and (verdict["pass"] or not pub["require_checker_pass"]):
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
