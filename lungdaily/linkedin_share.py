"""Share new articles on the LungDaily LinkedIn page. Runs AFTER the site is deployed
(python -m lungdaily.run linkedin), so the share-card image is already online.

Route 1 (in use): Make.com. Secret MAKE_LINKEDIN_WEBHOOK = URL of a Make "Custom webhook".
The Make scenario downloads image_url and creates a Company Image Post with `text`.
Make holds LinkedIn's page-posting approval, so no LinkedIn developer app review is needed.

Route 2 (only if LinkedIn ever approves our own app): Posts API, organisation as author.

Needs a LinkedIn app with the Community Management API product and a token with the
`w_organization_social` scope, issued by a page admin. Secrets:

* LINKEDIN_ORG_ID          numeric id of the LungDaily company page (or amplify.linkedin_org_id in settings.yaml)
* LINKEDIN_ACCESS_TOKEN    access token (lasts 60 days), OR, preferred, all three of:
* LINKEDIN_REFRESH_TOKEN, LINKEDIN_CLIENT_ID, LINKEDIN_CLIENT_SECRET
  -> a fresh access token is fetched on every run (refresh tokens last a year).

Each post: headline, short summary, source credit, hashtags, and a link card with the
share image (uploaded from site/cards/<slug>.png).

If the article's source is itself a LinkedIn post (and amplify.linkedin_reshare_source is on),
the page reposts the original instead, with our text and article link on top. LinkedIn then
shows the original author, their photo and their own visual. If the repost is refused, it falls
back to the normal link post.
"""
from __future__ import annotations

import os
import re

import requests

from .util import ROOT, log

API = "https://api.linkedin.com/rest"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
_cached_token: str | None = None


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def org_id() -> str:
    """LungDaily page id: LINKEDIN_ORG_ID secret, else amplify.linkedin_org_id in settings.yaml."""
    if _env("LINKEDIN_ORG_ID"):
        return _env("LINKEDIN_ORG_ID")
    from .util import settings
    return str((settings().get("amplify") or {}).get("linkedin_org_id") or "").strip()


def configured() -> bool:
    has_token = _env("LINKEDIN_ACCESS_TOKEN") or all(
        _env(k) for k in ("LINKEDIN_REFRESH_TOKEN", "LINKEDIN_CLIENT_ID", "LINKEDIN_CLIENT_SECRET"))
    return bool(org_id() and has_token)


def access_token() -> str | None:
    global _cached_token
    if _cached_token:
        return _cached_token
    if _env("LINKEDIN_REFRESH_TOKEN") and _env("LINKEDIN_CLIENT_ID") and _env("LINKEDIN_CLIENT_SECRET"):
        r = requests.post(TOKEN_URL, data={
            "grant_type": "refresh_token",
            "refresh_token": _env("LINKEDIN_REFRESH_TOKEN"),
            "client_id": _env("LINKEDIN_CLIENT_ID"),
            "client_secret": _env("LINKEDIN_CLIENT_SECRET"),
        }, timeout=30)
        if r.ok and r.json().get("access_token"):
            _cached_token = r.json()["access_token"]
            return _cached_token
        log.warning("LinkedIn: token refresh failed: %s %s", r.status_code, " ".join(r.text.split())[:300])
    _cached_token = _env("LINKEDIN_ACCESS_TOKEN") or None
    return _cached_token


def _headers(token: str, version: str) -> dict:
    return {"Authorization": f"Bearer {token}", "LinkedIn-Version": version,
            "X-Restli-Protocol-Version": "2.0.0", "Content-Type": "application/json"}


# LinkedIn "little text" format: these characters must be escaped in commentary.
_RESERVED = re.compile(r"([\\|{}@\[\]()<>#*_~])")


def escape(text: str) -> str:
    return _RESERVED.sub(r"\\\1", text)


def hashtag(tag: str) -> str:
    word = re.sub(r"[^0-9A-Za-z]", "", tag.lstrip("#"))
    return f"{{hashtag|\\#|{word}}}" if word else ""


def commentary(rec: dict, hashtags: list[str]) -> str:
    """Plain, professional post text for LinkedIn (no @mentions; those need member/org URNs)."""
    parts = [escape(rec["headline"])]
    if rec.get("dek"):
        parts.append(escape(rec["dek"]))
    author = (rec.get("source") or {}).get("author_name")
    if author:
        parts.append(escape(f"Source: {author}"))
    tags = " ".join(h for h in (hashtag(t) for t in hashtags) if h)
    if tags:
        parts.append(tags)
    text = "\n\n".join(parts)
    return text[:2900]


def upload_image(path, owner: str, token: str, version: str) -> str | None:
    if not path.exists():
        return None
    r = requests.post(f"{API}/images?action=initializeUpload", headers=_headers(token, version),
                      json={"initializeUploadRequest": {"owner": owner}}, timeout=30)
    if not r.ok:
        log.warning("LinkedIn: image init failed: %s %s", r.status_code, " ".join(r.text.split())[:300])
        return None
    val = r.json().get("value", {})
    up = requests.put(val["uploadUrl"], data=path.read_bytes(),
                      headers={"Authorization": f"Bearer {token}"}, timeout=60)
    if not up.ok:
        log.warning("LinkedIn: image upload failed: %s", up.status_code)
        return None
    return val.get("image")


def reshare_parent(rec: dict) -> str | None:
    """URN of the source LinkedIn post, if it can be reposted (share or ugcPost URNs only)."""
    src = rec.get("source") or {}
    if src.get("source") != "linkedin":
        return None
    urn = ((src.get("extra") or {}).get("urn") or "").strip()
    return urn if urn.startswith(("urn:li:share:", "urn:li:ugcPost:")) else None


def post(rec: dict, url: str, cfg: dict, hashtags: list[str]) -> str | None:
    """Publish one article. Returns the post URN, or None on failure / not configured."""
    if not configured():
        log.info("LinkedIn posting: credentials missing, skipping")
        return None
    token = access_token()
    if not token:
        return None
    version = str(cfg.get("linkedin_api_version") or "202610")
    owner = f"urn:li:organization:{org_id()}"
    article = {"source": url, "title": rec["headline"][:200], "description": (rec.get("dek") or "")[:250]}
    image = upload_image(ROOT / "site" / "cards" / f"{rec['slug']}.png", owner, token, version)
    if image:
        article["thumbnail"] = image
    body = {
        "author": owner,
        "commentary": commentary(rec, hashtags),
        "visibility": "PUBLIC",
        "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
        "content": {"article": article},
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    parent = reshare_parent(rec) if cfg.get("linkedin_reshare_source", True) else None
    if parent:
        reshare = {k: v for k, v in body.items() if k != "content"}
        reshare["commentary"] = (body["commentary"] + "\n\n" + f"Read on LungDaily: {url}")[:2990]
        reshare["reshareContext"] = {"parent": parent}
        r = requests.post(f"{API}/posts", headers=_headers(token, version), json=reshare, timeout=30)
        if r.ok:
            return r.headers.get("x-restli-id") or r.headers.get("x-linkedin-id") or "reposted"
        log.warning("LinkedIn repost of %s refused (%s %s) — posting a link instead",
                    parent, r.status_code, " ".join(r.text.split())[:200])
    r = requests.post(f"{API}/posts", headers=_headers(token, version), json=body, timeout=30)
    if r.status_code >= 400:
        hint = {401: " — token expired or invalid: refresh LINKEDIN_* secrets",
                403: " — the app needs Community Management API and the token needs w_organization_social, "
                     "issued by an admin of the LungDaily page",
                426: " — LinkedIn-Version too old: update amplify.linkedin_api_version in settings.yaml"}.get(r.status_code, "")
        log.warning("LinkedIn posting failed: %s %s%s", r.status_code, " ".join(r.text.split())[:400], hint)
        return None
    return r.headers.get("x-restli-id") or r.headers.get("x-linkedin-id") or "posted"


# --- Make.com route + post-deploy runner ------------------------------------------------

def make_webhook() -> str:
    return _env("MAKE_LINKEDIN_WEBHOOK")


def plain_text(rec: dict, url: str, hashtags: list[str]) -> str:
    parts = [rec["headline"]]
    if rec.get("dek"):
        parts.append(rec["dek"])
    author = (rec.get("source") or {}).get("author_name")
    if author:
        parts.append(f"Source: {author}")
    parts.append(f"Read more: {url}")
    tags = " ".join("#" + re.sub(r"[^0-9A-Za-z]", "", t.lstrip("#")) for t in hashtags if t)
    if tags.strip("# "):
        parts.append(tags)
    return "\n\n".join(parts)[:2900]


def linkedin_mentions(rec: dict, limit: int = 3) -> list[tuple[str, str, bool]]:
    """Organisations to tag on LinkedIn: the source author (if an organisation with a known
    linkedin_id in voices.yaml), then voices named in the article. Returns [(name, urn, is_source)].
    LinkedIn's API can tag organisations only, not individual people."""
    from .util import load_yaml
    voices = [v for v in load_yaml("voices.yaml").get("voices", []) if v.get("linkedin_id")]
    opt = load_yaml("optout.yaml")
    blocked = {u.lower().rstrip("/") for u in opt.get("linkedin_urls") or []} | {n.lower() for n in opt.get("names") or []}
    src = rec.get("source") or {}
    out: list[tuple[str, str, bool]] = []

    def add(v, is_source=False):
        urn = f"urn:li:organization:{str(v['linkedin_id']).strip()}"
        if (v.get("linkedin") or "").lower().rstrip("/") in blocked or v.get("name", "").lower() in blocked:
            return
        if urn not in {m[1] for m in out}:
            out.append((v.get("linkedin_name") or v["name"], urn, is_source))

    def names(v):
        return [n for n in [v.get("name", ""), v.get("linkedin_name", "")] + list(v.get("aliases") or []) if n]

    handle = (src.get("author_handle") or "").lower()
    author = (src.get("author_name") or "").lower()
    for v in voices:
        if (handle and handle == (v.get("x") or "").lower()) or (author and author in [n.lower() for n in names(v)]):
            add(v, is_source=True)
    text = " ".join([rec.get("headline", ""), rec.get("body", ""), " ".join(rec.get("people") or [])])
    for v in voices:
        if any(re.search(rf"(?<!\w){re.escape(n)}(?!\w)", text) for n in names(v)):
            add(v)
    return out[:limit]


def little_text(rec: dict, url: str, hashtags: list[str], mentions: list[tuple[str, str, bool]]) -> str:
    """Post text in LinkedIn's little-text format: escaped text, clickable organisation tags
    and clickable hashtags."""
    parts = [escape(rec["headline"])]
    if rec.get("dek"):
        parts.append(escape(rec["dek"]))
    src = rec.get("source") or {}
    author = src.get("author_name")
    tag = lambda m: f"@[{escape(m[0])}]({m[1]})"
    src_mention = next((m for m in mentions if m[2]), None)
    if author:
        parts.append("Source: " + (tag(src_mention) if src_mention else escape(author)))
    others = [m for m in mentions if not m[2]]
    if others:
        parts.append("Featuring: " + " ".join(tag(m) for m in others))
    parts.append(escape("Read more: ") + url)
    hs = " ".join(h for h in (hashtag(t) for t in hashtags) if h)
    if hs:
        parts.append(hs)
    return "\n\n".join(parts)[:2900]


def post_via_make(rec: dict, url: str, image_url: str, hashtags: list[str], max_mentions: int = 3) -> str | None:
    text = plain_text(rec, url, hashtags)
    mentions = linkedin_mentions(rec, max_mentions)
    payload = {
        "slug": rec["slug"], "url": url, "title": rec["headline"], "summary": rec.get("dek", ""),
        "text": text, "text_escaped": little_text(rec, url, hashtags, mentions),
        "image_url": image_url, "category": rec.get("category_label") or rec.get("category"),
    }
    r = requests.post(make_webhook(), json=payload, timeout=60)
    if r.status_code >= 400:
        log.warning("LinkedIn via Make failed: %s %s", r.status_code, " ".join(r.text.split())[:300])
        return None
    return "make"


def run_pending() -> int:
    """Post recently published articles that are not yet on LinkedIn. Returns number posted."""
    import datetime as dt
    from .amplify import HASHTAGS
    from .util import POSTS, now, parse_date, read_json, settings, write_json
    cfg = settings()
    acfg, base = cfg["amplify"], cfg["site"]["base_url"].rstrip("/")
    if not acfg.get("linkedin_post"):
        return 0
    use_make, use_api = bool(make_webhook()), configured()
    if not (use_make or use_api):
        log.info("LinkedIn: no MAKE_LINKEDIN_WEBHOOK or LinkedIn API credentials, skipping")
        return 0
    window = now() - dt.timedelta(hours=int(acfg.get("linkedin_window_hours", 36)))
    day_ago = now() - dt.timedelta(hours=24)
    recs = []
    for p in POSTS.glob("*.json"):
        rec = read_json(p, {})
        if rec.get("status") == "published":
            recs.append((p, rec))
    done_24h = sum(1 for _, r in recs if (parse_date((r.get("shared") or {}).get("linkedin_at")) or window) > day_ago)
    cap = int(acfg.get("linkedin_max_per_day", 8))
    todo = [(p, r) for p, r in recs
            if "linkedin" not in (r.get("shared") or {}) and r.get("linkedin_attempts", 0) < 4
            and (parse_date(r.get("published_at")) or window) > window]
    todo.sort(key=lambda pr: pr[1].get("published_at", ""))
    posted = 0
    stamp = now().strftime("%Y%m%d%H%M")
    for p, rec in todo:
        if done_24h + posted >= cap:
            log.info("LinkedIn: daily cap of %d reached, rest wait for the next run", cap)
            break
        url = f"{base}/news/{rec['slug']}/"
        tags = [HASHTAGS[t][1:] for t in rec.get("tags") or [] if t in HASHTAGS][: int(acfg.get("linkedin_hashtags", 3))]
        if use_make:
            res = post_via_make(rec, url, f"{base}/cards/{rec['slug']}.png?v={stamp}", tags,
                                int(acfg.get("linkedin_max_mentions", 3)))
        else:
            res = post(rec, url, acfg, tags)
        shared = rec.setdefault("shared", {})
        if res:
            shared["linkedin"] = res
            shared["linkedin_at"] = now().isoformat(timespec="seconds")
            posted += 1
            log.info("LinkedIn: shared %s", rec["slug"])
        else:
            rec["linkedin_attempts"] = rec.get("linkedin_attempts", 0) + 1
        write_json(p, rec)
    return posted


def send_test() -> bool:
    """Send the newest article to the Make webhook (so Make learns the data structure). Marks nothing."""
    from .amplify import HASHTAGS
    from .util import POSTS, now, read_json, settings
    if not make_webhook():
        log.warning("LinkedIn test: MAKE_LINKEDIN_WEBHOOK secret is not set")
        return False
    cfg = settings()
    base = cfg["site"]["base_url"].rstrip("/")
    recs = [read_json(p, {}) for p in POSTS.glob("*.json")]
    recs = sorted((r for r in recs if r.get("status") == "published"), key=lambda r: r.get("published_at", ""))
    if not recs:
        return False
    rec = recs[-1]
    tags = [HASHTAGS[t][1:] for t in rec.get("tags") or [] if t in HASHTAGS][:3]
    ok = post_via_make(rec, f"{base}/news/{rec['slug']}/",
                       f"{base}/cards/{rec['slug']}.png?v={now().strftime('%Y%m%d%H%M')}", tags)
    log.info("LinkedIn test sent to Make: %s (%s)", bool(ok), rec["slug"])
    return bool(ok)
