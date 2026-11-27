"""Portal pages built from published articles: topic hubs, voices & organisations, trackers,
search index, today's top 5, caregivers and source-type labels. No AI writing — everything here
is selection and layout of already-checked articles plus config lists of sourced reference links."""
from __future__ import annotations

import datetime as dt
import re
import urllib.parse

from .util import load_yaml, now, settings, slugify

# ---------------------------------------------------------------------------------- source types

PRESS_WIRES = ("prnewswire.", "businesswire.", "globenewswire.", "newswire.", "accesswire.", "einpresswire.",
               "stocktitan.", "eurekalert.")
PREPRINTS = ("medrxiv.", "biorxiv.", "researchsquare.", "ssrn.", "preprints.org", "arxiv.")
JOURNALS = ("doi.org", "thelancet.", "nejm.", "ersjournals.", "atsjournals.", "bmj.", "jamanetwork.", "nature.com",
            "wiley.", "springer.", "sciencedirect.", "mdpi.", "frontiersin.", "journal.chestnet", "cell.com",
            "academic.oup.", "tandfonline.", "plos.", "biomedcentral.", "ncbi.nlm.nih.gov", "pubmed.")


def _host(url: str) -> str:
    try:
        return urllib.parse.urlparse(url or "").netloc.lower().split(":")[0]
    except ValueError:
        return ""


def source_type(p: dict) -> dict:
    """What the article is based on, so readers can weigh it: {key, label}."""
    src = p.get("source") or {}
    kind = src.get("source")
    host = _host(src.get("url", ""))
    if p.get("kind") == "qa":
        return {"key": "qa", "label": "Email Q&A"}
    if kind == "pubmed" or any(j in host for j in JOURNALS):
        return {"key": "paper", "label": "Peer-reviewed paper"}
    if any(x in host for x in PREPRINTS):
        return {"key": "preprint", "label": "Preprint (not peer reviewed)"}
    if kind in ("x", "linkedin"):
        return {"key": "social", "label": "Social media post"}
    if any(x in host for x in PRESS_WIRES):
        return {"key": "press", "label": "Press release"}
    return {"key": "news", "label": "News report"}


# ---------------------------------------------------------------------------------- topic hubs

def _text(p: dict) -> str:
    return f" {p.get('headline', '')} {p.get('dek', '')} "


def hub_matches(hub: dict, p: dict) -> bool:
    if set(hub.get("tags") or []) & set(p.get("tags") or []):
        return True
    t = _text(p)
    for k in hub.get("keywords") or []:
        # short all-caps keywords (COPD, NSCLC, IPF) are matched as words, case-sensitive
        if k.strip().isupper() and len(k.strip()) <= 6:
            if re.search(rf"\b{re.escape(k.strip())}\b", t):
                return True
        elif k.lower() in t.lower():
            return True
    return False


def hubs() -> list[dict]:
    return list(load_yaml("hubs.yaml").get("hubs") or [])


def hub_pages(posts: list[dict], days_by_slug: dict | None = None) -> list[dict]:
    """One dict per hub: posts, papers, approvals, guidelines, voices, days."""
    out, vs = [], public_voices()
    for h in hubs():
        sel = [p for p in posts if hub_matches(h, p)]
        days = [days_by_slug[s] for s in h.get("days") or [] if days_by_slug and s in days_by_slug]
        facts = [dict(f, day=d["name"]) for d in days for f in (d.get("facts") or [])]
        out.append(dict(h, posts=sel, count=len(sel),
                        papers=[p for p in sel if p["category"] == "research"][:6],
                        approvals=[p for p in sel if p["category"] == "approvals"][:6],
                        guidelines=[p for p in sel if is_guideline(p)][:6],
                        voices=coverage_voices(sel, limit=12, voices=vs), days=days, facts=facts[:4]))
    return out


# ---------------------------------------------------------------------------------- voices

def _excluded() -> set[str]:
    return {h.lower() for h in (settings().get("follow") or {}).get("exclude") or []}


def voice_slug(v: dict) -> str:
    return slugify(v["name"], 60)


def public_voices() -> list[dict]:
    """voices.yaml entries that may get a page: never the site's own people (follow.exclude), never opt-outs."""
    opt = load_yaml("optout.yaml")
    names = {n.lower() for n in opt.get("names") or []}
    handles = {h.lower().lstrip("@") for h in opt.get("x_handles") or []}
    skip = _excluded()
    out = []
    for v in load_yaml("voices.yaml").get("voices") or []:
        x = (v.get("x") or "").lower()
        if x in skip or x in handles or v.get("name", "").lower() in names:
            continue
        out.append(v)
    return out


def _voice_kinds() -> dict:
    """name -> 'person' | 'organisation', from the section comments in voices.yaml
    ('# --- People', '# --- Individual clinicians ...', '# --- LinkedIn-only individuals')."""
    from .util import CONFIG
    kinds, cur = {}, "organisation"
    for line in (CONFIG / "voices.yaml").read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("# ---"):
            low = s.lower()
            cur = "person" if ("people" in low or "individual" in low) else "organisation"
        m = re.match(r'-\s*name:\s*["\']?(.+?)["\']?\s*(#.*)?$', s)
        if m:
            kinds[m.group(1)] = cur
    return kinds


def _voice_names(v: dict) -> list[str]:
    return [n for n in [v.get("name", ""), v.get("linkedin_name", "")] + list(v.get("aliases") or []) if n]


def _source_voice_name(p: dict) -> str:
    """Cached per article (source_voice reads voices.yaml on every call)."""
    if "_sv" not in p:
        from .featured import source_voice
        sv = source_voice(p)
        p["_sv"] = sv.get("name", "") if sv else ""
    return p["_sv"]


def voice_matches(v: dict, p: dict) -> bool:
    """Article is based on this voice's post, or names the voice (people list, or full name in headline/dek)."""
    if _source_voice_name(p) == v.get("name"):
        return True
    people = {n.lower() for n in p.get("people") or []}
    for n in _voice_names(v):
        if n.lower() in people:
            return True
        if len(n) > 6 and n.lower() in _text(p).lower():   # skip short aliases (WHO, ERS) in free text
            return True
    return False


def voice_pages(posts: list[dict], min_articles: int = 1) -> list[dict]:
    out = []
    for v in public_voices():
        sel = [p for p in posts if voice_matches(v, p)]
        if len(sel) < min_articles:
            continue
        is_org = _voice_kinds().get(v["name"], "organisation") == "organisation" and "/in/" not in str(v.get("linkedin") or "")
        out.append({"name": v["name"], "slug": voice_slug(v), "x": v.get("x"), "linkedin": v.get("linkedin"),
                    "description": v.get("description") or "", "posts": sel, "count": len(sel),
                    "latest": sel[0]["date"] if sel else None,
                    "kind": "organisation" if is_org else "person"})
    out.sort(key=lambda r: (-r["count"], r["name"]))
    return out


def coverage_voices(sel: list[dict], limit: int = 12, voices: list[dict] | None = None) -> list[dict]:
    """Voices (with a page) most covered in these articles."""
    counts = []
    for v in voices if voices is not None else public_voices():
        n = sum(1 for p in sel if voice_matches(v, p))
        if n:
            counts.append((n, v["name"], voice_slug(v)))
    counts.sort(key=lambda t: (-t[0], t[1]))
    return [{"name": nm, "slug": sl, "count": n} for n, nm, sl in counts[:limit]]


# ---------------------------------------------------------------------------------- trackers

REGULATORS = [
    ("FDA", r"\b(?:US ?)?FDA\b|Food and Drug Administration"),
    ("EMA / EC", r"\bEMA\b|\bCHMP\b|European Medicines Agency|European Commission"),
    ("MHRA", r"\bMHRA\b"),
    ("NMPA (China)", r"\bNMPA\b|National Medical Products Administration"),
    ("PMDA / MHLW (Japan)", r"\bPMDA\b|\bMHLW\b|Japan'?s Ministry of Health"),
    ("Health Canada", r"Health Canada"),
    ("TGA (Australia)", r"\bTGA\b|Therapeutic Goods Administration"),
    ("MFDS (Korea)", r"\bMFDS\b|\bin (?:South )?Korea\b"),
    ("NICE", r"\bNICE\b"),
]
STAGES = [
    ("Rejection", r"reject|complete response letter|\bCRL\b|refus|negative opinion|declines? to approve"),
    ("Regulatory update", r"feedback|alignment|heads? toward|possible|planned"),
    ("Approval", r"\bapprov|\bauthori[sz]|\bclears?\b"),
    ("Recommendation", r"recommend|positive opinion|\bCHMP\b"),
    ("Filing", r"accept|filing|submission|priority review|\breview\b|breakthrough|fast track|orphan"),
    ("Trial start", r"recruit|enrol|begins dosing|first patient|initiat|launch|\bto test\b|\bstarts?\b"),
    ("Trial result", r"topline|met (?:its |the )?primary|endpoint|results|reports|shows|data\b"),
]


def regulator(p: dict) -> str:
    """Regulator named first in the headline (else the dek)."""
    for t in (p.get("headline", ""), p.get("dek", "")):
        hits = [(m.start(), name) for name, rx in REGULATORS for m in [re.search(rx, t)] if m]
        if hits:
            return min(hits)[1]
    return ""


def stage(p: dict) -> str:
    for t in (p.get("headline", ""), p.get("dek", "")):
        for name, rx in STAGES:
            if re.search(rx, t, re.I):
                return name
    return "Update"


def approvals_rows(posts: list[dict], limit: int = 150) -> list[dict]:
    rows, hub_list = [], hubs()
    for p in posts:
        if p["category"] != "approvals":
            continue
        topics = [h["name"] for h in hub_list if hub_matches(h, p)][:2]
        rows.append({"post": p, "regulator": regulator(p) or "—", "stage": stage(p), "topics": topics})
    return rows[:limit]


GUIDELINE_RX = re.compile(r"guideline|recommendations?\b|statement|consensus|GOLD 20\d\d|GINA 20\d\d|strategy report", re.I)


def is_guideline(p: dict) -> bool:
    return "guidelines" in (p.get("tags") or []) or bool(GUIDELINE_RX.search(f"{p.get('headline', '')} {p.get('dek', '')}"))


# ---------------------------------------------------------------------------------- today, search, caregivers

def top_today(posts: list[dict], n: int = 5) -> tuple[list[dict], int]:
    """Best-scored articles of the last 24 h (widening to 48/72 h on quiet days). Returns (posts, hours)."""
    for hours in (24, 48, 72):
        cut = now() - dt.timedelta(hours=hours)
        sel = [p for p in posts if p["date"] >= cut]
        if len(sel) >= n or hours == 72:
            sel.sort(key=lambda p: (-(p.get("score") or 0), -p["date"].timestamp()))
            out, authors = [], set()
            for p in sel:   # one story per source author, so the five are five different voices
                a = ((p.get("source") or {}).get("author_name") or p["slug"]).lower()
                if a in authors:
                    continue
                authors.add(a)
                out.append(p)
            return out[:n], hours
    return [], 24


def search_index(posts: list[dict], hub_list: list[dict]) -> list[dict]:
    names = {}
    for h in hub_list:
        for p in h["posts"]:
            names.setdefault(p["slug"], []).append(h["name"])
    return [{"s": p["slug"], "h": p["headline"], "d": p["dek"], "c": p["category_label"],
             "t": " ".join(p.get("tags") or []) + " " + " ".join(names.get(p["slug"], [])),
             "p": " ".join(p.get("people") or []), "a": (p.get("source") or {}).get("author_name") or "",
             "dt": p["date"].strftime("%Y-%m-%d")} for p in posts]


def caregivers() -> dict:
    return load_yaml("caregivers.yaml") or {}


def caregiver_posts(posts: list[dict], cfg: dict, limit: int = 12) -> list[dict]:
    kws = [k.lower() for k in cfg.get("news_keywords") or []]
    out = []
    for p in posts:
        t = f"{p.get('headline', '')} {p.get('dek', '')}".lower()
        if any(k in t for k in kws):
            out.append(p)
    return out[:limit]
