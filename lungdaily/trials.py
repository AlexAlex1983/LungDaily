"""Recruiting respiratory trials from ClinicalTrials.gov (public API v2, no key) for /trials/.

Refreshed at most once a day during a site build (cache: data/trials.json); a failed fetch keeps the
last good list. Only registry fields are shown (title, phase, sponsor, countries, first posted) with a
link to the registry record — no AI text.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import urllib.parse
import urllib.request

from .util import DATA, log, now, parse_date, read_json, write_json

CACHE = DATA / "trials.json"
API = "https://clinicaltrials.gov/api/v2/studies"
CONDITIONS = [  # (hub slug, label, ClinicalTrials.gov condition query)
    ("copd", "COPD", "COPD"),
    ("asthma", "Asthma", "asthma"),
    ("lung-cancer", "Lung cancer", "lung cancer"),
    ("ild", "ILD & pulmonary fibrosis", "interstitial lung disease OR pulmonary fibrosis"),
    ("bronchiectasis", "Bronchiectasis", "bronchiectasis"),
    ("tb", "Tuberculosis", "tuberculosis"),
]
FIELDS = ("NCTId,BriefTitle,Phase,LeadSponsorName,LocationCountry,StudyFirstPostDate,OverallStatus,"
          "Condition,EnrollmentCount,StudyType")
PER_CONDITION = 12
MAX_AGE_H = 20


def parse(payload: dict) -> list[dict]:
    """API v2 JSON -> rows."""
    rows = []
    for st in payload.get("studies") or []:
        ps = st.get("protocolSection") or {}
        ident = ps.get("identificationModule") or {}
        status = ps.get("statusModule") or {}
        design = ps.get("designModule") or {}
        sponsor = ((ps.get("sponsorCollaboratorsModule") or {}).get("leadSponsor") or {}).get("name", "")
        locs = (ps.get("contactsLocationsModule") or {}).get("locations") or []
        countries = sorted({l.get("country") for l in locs if l.get("country")})
        nct = ident.get("nctId")
        if not nct:
            continue
        phases = [p.replace("PHASE", "Phase ").replace("EARLY_", "Early ").replace("NA", "n/a").title()
                  for p in design.get("phases") or []]
        rows.append({
            "nct": nct, "title": ident.get("briefTitle", ""), "phase": " / ".join(phases) or "—",
            "sponsor": sponsor, "countries": countries[:6], "n_countries": len(countries),
            "first_posted": (status.get("studyFirstPostDateStruct") or {}).get("date", ""),
            "status": status.get("overallStatus", ""),
            "enrollment": (design.get("enrollmentInfo") or {}).get("count"),
            "url": f"https://clinicaltrials.gov/study/{nct}",
        })
    return rows


def _fetch(cond: str) -> list[dict]:
    q = urllib.parse.urlencode({
        "query.cond": cond, "filter.overallStatus": "RECRUITING", "sort": "StudyFirstPostDate:desc",
        "pageSize": PER_CONDITION, "format": "json", "fields": FIELDS})
    req = urllib.request.Request(f"{API}?{q}", headers={"User-Agent": "LungDaily/1.0 (+https://lungdaily.org)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return parse(json.loads(r.read().decode("utf-8")))


def refresh(force: bool = False) -> dict:
    """Update the cache when older than MAX_AGE_H. Never raises."""
    cache = read_json(CACHE, {})
    if os.environ.get("LUNGDAILY_FAKE_LLM") or os.environ.get("LUNGDAILY_OFFLINE"):
        return cache
    fetched = parse_date(cache.get("fetched_at")) if cache.get("fetched_at") else None
    if not force and fetched and now() - fetched < dt.timedelta(hours=MAX_AGE_H):
        return cache
    groups, ok = [], 0
    old = {g["slug"]: g for g in cache.get("groups") or []}
    for slug, label, cond in CONDITIONS:
        try:
            rows = _fetch(cond)
            ok += 1
        except Exception as e:
            log.warning("ClinicalTrials.gov fetch failed for %s: %s", cond, e)
            rows = (old.get(slug) or {}).get("rows") or []
        groups.append({"slug": slug, "label": label, "rows": rows})
    if ok:
        cache = {"fetched_at": now().isoformat(timespec="seconds"), "groups": groups}
        write_json(CACHE, cache)
        log.info("Trials: %d groups refreshed from ClinicalTrials.gov", ok)
    return cache
