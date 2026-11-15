"""Monthly reports: "The Month in Respiratory Health: October 2026" (/reports/2026-10/).

Built from published articles only — no AI writing: per topic, the month's strongest stories with
their verified verbatim key quotes ("who said what"), the top-rated studies and the most-featured
voices. Original, citable pages that other sites can link to.
"""
from __future__ import annotations

from .util import now

TOPICS = [  # (tag, label) in display order
    ("copd", "COPD"), ("asthma", "Asthma"), ("lung-cancer", "Lung cancer"), ("tb", "Tuberculosis"),
    ("air-pollution", "Air pollution"), ("tobacco", "Tobacco"), ("vaping", "Vaping"), ("ild", "ILD & fibrosis"),
    ("bronchiectasis", "Bronchiectasis"), ("ai", "AI in lung health"), ("policy", "Policy"), ("global-health", "Global health"),
]
MIN_ARTICLES = 10      # a month with less coverage gets no report


def month_key(d) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def build_reports(posts: list[dict], per_topic: int = 5, include_current: bool = False) -> list[dict]:
    """posts: build.load_posts() output (with 'date'). Returns newest first."""
    cur = month_key(now())
    by_month: dict = {}
    for p in posts:
        k = month_key(p["date"])
        if k == cur and not include_current:
            continue
        by_month.setdefault(k, []).append(p)
    out = []
    for key, sel in sorted(by_month.items(), reverse=True):
        if len(sel) < MIN_ARTICLES:
            continue
        y, m = int(key[:4]), int(key[5:])
        label = sel[0]["date"].replace(day=1).strftime("%B") + f" {y}"
        best = sorted(sel, key=lambda p: (-float(p.get("score") or 0), p["date"]))
        sections, counts = [], []
        for tag, tlabel in TOPICS:
            tp = [p for p in best if tag in (p.get("tags") or [])]
            if tp:
                counts.append({"label": tlabel, "n": len(tp), "tag": tag})
            quoted = [p for p in tp if p.get("key_quote")]
            pick = (quoted + [p for p in tp if p not in quoted])[:per_topic]
            if len(tp) >= 2:
                sections.append({"tag": tag, "label": tlabel, "posts": pick})
        voices: dict = {}
        from .util import settings
        skip = {h.lower() for h in (settings().get("follow") or {}).get("exclude") or []}   # never rank the site's own people
        for p in sel:
            src = p.get("source") or {}
            n = src.get("author_name")
            if (src.get("author_handle") or "").lower() in skip:
                continue
            if n and src.get("source") in ("x", "linkedin"):
                voices[n] = voices.get(n, 0) + 1
        out.append({
            "key": key, "year": y, "month": m, "month_label": label,
            "title": f"The Month in Respiratory Health: {label}",
            "total": len(sel), "sources": len({(p.get("source") or {}).get("author_name") for p in sel}),
            "top_topics": sorted(counts, key=lambda c: -c["n"])[:5],
            "sections": sections,
            "studies": [p for p in best if p["category"] == "research"][:10],
            "voices": [{"name": k, "n": v} for k, v in sorted(voices.items(), key=lambda kv: -kv[1])[:15]],
        })
    return out
