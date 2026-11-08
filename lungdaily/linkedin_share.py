"""Share new articles on the LungDaily LinkedIn page (Posts API, organisation as author).

Needs a LinkedIn app with the Community Management API product and a token with the
`w_organization_social` scope, issued by a page admin. Secrets:

* LINKEDIN_ORG_ID          numeric id of the LungDaily company page (from the admin URL)
* LINKEDIN_ACCESS_TOKEN    access token (lasts 60 days), OR, preferred, all three of:
* LINKEDIN_REFRESH_TOKEN, LINKEDIN_CLIENT_ID, LINKEDIN_CLIENT_SECRET
  -> a fresh access token is fetched on every run (refresh tokens last a year).

Each post: headline, short summary, source credit, hashtags, and a link card with the
branded share image (uploaded from site/cards/<slug>.png).
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


def configured() -> bool:
    has_token = _env("LINKEDIN_ACCESS_TOKEN") or all(
        _env(k) for k in ("LINKEDIN_REFRESH_TOKEN", "LINKEDIN_CLIENT_ID", "LINKEDIN_CLIENT_SECRET"))
    return bool(_env("LINKEDIN_ORG_ID") and has_token)


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


def post(rec: dict, url: str, cfg: dict, hashtags: list[str]) -> str | None:
    """Publish one article. Returns the post URN, or None on failure / not configured."""
    if not configured():
        log.info("LinkedIn posting: credentials missing, skipping")
        return None
    token = access_token()
    if not token:
        return None
    version = str(cfg.get("linkedin_api_version") or "202610")
    owner = f"urn:li:organization:{_env('LINKEDIN_ORG_ID')}"
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
    r = requests.post(f"{API}/posts", headers=_headers(token, version), json=body, timeout=30)
    if r.status_code >= 400:
        hint = {401: " — token expired or invalid: refresh LINKEDIN_* secrets",
                403: " — the app needs Community Management API and the token needs w_organization_social, "
                     "issued by an admin of the LungDaily page",
                426: " — LinkedIn-Version too old: update amplify.linkedin_api_version in settings.yaml"}.get(r.status_code, "")
        log.warning("LinkedIn posting failed: %s %s%s", r.status_code, " ".join(r.text.split())[:400], hint)
        return None
    return r.headers.get("x-restli-id") or r.headers.get("x-linkedin-id") or "posted"
