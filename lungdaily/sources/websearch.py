"""Web news scout: Claude searches the web for fresh lung-health news, using the same criteria
as the editor's digest, limited to COPD, asthma, air pollution and its health effects, and lung cancer
(reputable outlets; concrete facts). Each find is checked against the real article
page, and only items whose page text can be read are kept, so articles are always built on the
publisher's own text, never on the model's summary alone.

Runs only at sources.web_search_hours (local time) to keep cost down.
"""
from __future__ import annotations

import hashlib

from ..llm import LLMError, ask_web
from ..util import DATA, iso, load_yaml, log, now, parse_date, read_json, settings, word_count, write_json
from . import make_item

STATE = DATA / "websearch.json"

SYSTEM = """You are the news scout for LungDaily, a respiratory-health news site.
Use web search to find the most important lung-health NEWS published in the last {hours} hours.

Cover ONLY these areas (search each separately):
- COPD (GOLD and other guidelines, trials, approvals, diagnosis, burden)
- Asthma (GINA, biologics, trials, conference data)
- Air pollution and its health effects (PM2.5, wildfire smoke, new evidence on lung and other health outcomes)
- Lung cancer (screening, trials, approvals, survival data){extra}
Ignore other topics, including general policy news that is not about these four.

Rules:
- Reputable outlets only: medical/scientific journals (ERJ, Lancet, NEJM, AJRCCM, Chest, Thorax),
  professional medical news (AJMC, HCPLive, Medscape, MedPage Today, Healio, Pulmonology Advisor),
  wire services (Reuters, AP, AFP, ANI), WHO/UN agencies, Health Policy Watch, major newspapers,
  and the official sites of societies (ERS, ATS, GOLD, GINA, FIRS, IUATLD).
- No press-release farms, SEO blogs, product ads, or content farms.
- Give the ORIGINAL article URL on the publisher's own site (not Google News, not MSN, not aggregators).
- Each item must contain concrete facts: numbers, the journal or report, the date, who said it.
- Skip items already covered (list below) and opinion pieces with no news.
- Return at most {max_items} items, best first.

Reply with JSON only:
{{"items": [{{"title": "...", "url": "https://...", "publisher": "...", "published_date": "YYYY-MM-DD",
  "topic": "copd|asthma|air_pollution|lung_cancer", "summary": "3-5 factual sentences"}}]}}"""


def _due(cfg: dict) -> bool:
    hours = [int(h) for h in cfg["sources"].get("web_search_hours", [6, 15])]
    tz = cfg.get("publishing", {}).get("timezone", "Europe/Berlin")
    try:
        from zoneinfo import ZoneInfo
        local = now().astimezone(ZoneInfo(tz))
    except Exception:
        local = now()
    state = read_json(STATE, {})
    slot = f"{local.date()}:{local.hour}"
    if local.hour not in hours or state.get("last_slot") == slot:
        return False
    state["last_slot"] = slot
    write_json(STATE, state)
    return True


def _recent_titles(n: int = 40) -> list[str]:
    from ..collect import INBOX
    return [i.get("title", "")[:100] for i in read_json(INBOX, [])[-n:] if i.get("title")]


def _fake():
    return {"items": [{
        "title": "Test: GOLD 2027 report published", "url": "https://example.org/gold-2027",
        "publisher": "Example News", "published_date": now().date().isoformat(), "topic": "copd",
        "summary": "The GOLD 2027 report was published on 18 November 2026. It updates COPD diagnosis advice.",
    }]}


def search(cfg: dict | None = None) -> list[dict]:
    cfg = cfg or settings()
    ws = cfg.get("web_search") or {}
    extra = "".join(f"\n- {t}" for t in ws.get("extra_topics") or [])
    hours = int(ws.get("lookback_hours", 48))
    system = SYSTEM.format(hours=hours, extra=extra, max_items=int(ws.get("max_items", 12)))
    seen = "\n".join(f"- {t}" for t in _recent_titles()) or "- (none)"
    user = f"Today is {now().date().isoformat()}. Already covered:\n{seen}\n\nFind today's news."
    try:
        out = ask_web(system, user, max_uses=int(ws.get("max_searches", 8)), fake=_fake)
    except LLMError as e:
        log.warning("Web search failed: %s", e)
        return []
    return (out or {}).get("items", []) if isinstance(out, dict) else []


def to_items(found: list[dict], fetch=None) -> list[dict]:
    """Keep only finds whose article page can be read; the page text is what the writer uses."""
    from .page import fetch_text
    fetch = fetch or fetch_text
    blocked = ("news.google.", "msn.com", "bing.com/news", "yahoo.com/news", "newsbreak")
    topics = set((settings().get("web_search") or {}).get("topics") or [])
    items = []
    for f in found:
        if topics and f.get("topic") not in topics:
            continue
        url, title = (f.get("url") or "").strip(), (f.get("title") or "").strip()
        if not url.startswith("http") or not title or any(b in url for b in blocked):
            continue
        final, page = fetch(url)
        if word_count(page) < 60:
            log.info("Web search: page unreadable, skipped: %s", url)
            continue
        url = final or url
        items.append(make_item(
            "rss", hashlib.sha1(url.encode()).hexdigest()[:16], url=url, title=title,
            text=f"ARTICLE PAGE TEXT:\n{page}",          # publisher's own text only
            author_name=f.get("publisher") or "", published_at=iso(parse_date(f.get("published_date"))),
            extra={"feed": "Web search", "web_search": True, "topic": f.get("topic"),
                   "scout_summary": f.get("summary", ""), "page_fetched": True},
        ))
    return items


def collect(force: bool = False) -> list[dict]:
    cfg = settings()
    if not force and not _due(cfg):
        return []
    items = to_items(search(cfg))
    log.info("Web search: %d item(s) with readable pages", len(items))
    return items
