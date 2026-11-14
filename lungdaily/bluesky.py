"""Mirror new articles to Bluesky (AT Protocol): headline + hashtags + a link card with the share image.

Needs the secrets BLUESKY_HANDLE (e.g. lungdaily.bsky.social or lungdaily.org) and
BLUESKY_APP_PASSWORD (Bluesky -> Settings -> Privacy and security -> App passwords).
settings.yaml -> amplify.bluesky_post turns it on; runs after deploy so the article page is online.
"""
from __future__ import annotations

import datetime as dt
import io
import os

import requests

from .util import POSTS, log, now, parse_date, read_json, settings, write_json

PDS = "https://bsky.social"
MAX_GRAPHEMES = 300


def _session() -> dict | None:
    handle, pw = os.environ.get("BLUESKY_HANDLE"), os.environ.get("BLUESKY_APP_PASSWORD")
    if not handle or not pw:
        log.info("Bluesky: BLUESKY_HANDLE / BLUESKY_APP_PASSWORD not set, skipping")
        return None
    r = requests.post(f"{PDS}/xrpc/com.atproto.server.createSession",
                      json={"identifier": handle.lstrip("@"), "password": pw}, timeout=30)
    if r.status_code >= 400:
        log.warning("Bluesky login failed: %s %s", r.status_code, r.text[:200])
        return None
    return r.json()


def post_text(rec: dict, hashtags: list[str]) -> tuple[str, list[dict]]:
    """Headline and hashtags within 300 characters, with tag facets (byte offsets, UTF-8)."""
    tags = [h if h.startswith("#") else "#" + h for h in hashtags]
    head = rec["headline"].strip()
    while True:
        text = head + ("\n\n" + " ".join(tags) if tags else "")
        if len(text) <= MAX_GRAPHEMES:
            break
        if tags:
            tags.pop()
            continue
        head = head[: MAX_GRAPHEMES - 1].rstrip() + "…"
    facets, b = [], text.encode("utf-8")
    pos = 0
    for t in tags:
        tb = t.encode("utf-8")
        i = b.find(tb, pos)
        if i < 0:
            continue
        facets.append({"index": {"byteStart": i, "byteEnd": i + len(tb)},
                       "features": [{"$type": "app.bsky.richtext.facet#tag", "tag": t[1:]}]})
        pos = i + len(tb)
    return text, facets


def _thumb(slug: str, session: dict) -> dict | None:
    """Upload the article's share card (as JPEG, under Bluesky's ~1 MB blob limit)."""
    from .build import OUT
    path = OUT / "cards" / f"{slug}.png"
    if not path.exists():
        return None
    from PIL import Image
    buf = io.BytesIO()
    Image.open(path).convert("RGB").save(buf, "JPEG", quality=85, optimize=True)
    r = requests.post(f"{PDS}/xrpc/com.atproto.repo.uploadBlob", data=buf.getvalue(), timeout=60,
                      headers={"Authorization": f"Bearer {session['accessJwt']}", "Content-Type": "image/jpeg"})
    if r.status_code >= 400:
        log.warning("Bluesky image upload failed: %s %s", r.status_code, r.text[:200])
        return None
    return r.json().get("blob")


def post(rec: dict, url: str, hashtags: list[str], session: dict) -> str | None:
    text, facets = post_text(rec, hashtags)
    external = {"uri": url, "title": rec["headline"], "description": rec.get("dek", "")[:300]}
    blob = _thumb(rec["slug"], session)
    if blob:
        external["thumb"] = blob
    record = {"$type": "app.bsky.feed.post", "text": text, "facets": facets, "langs": ["en"],
              "createdAt": now().astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
              "embed": {"$type": "app.bsky.embed.external", "external": external}}
    r = requests.post(f"{PDS}/xrpc/com.atproto.repo.createRecord", timeout=30,
                      headers={"Authorization": f"Bearer {session['accessJwt']}"},
                      json={"repo": session["did"], "collection": "app.bsky.feed.post", "record": record})
    if r.status_code >= 400:
        log.warning("Bluesky post failed: %s %s", r.status_code, r.text[:300])
        return None
    return r.json().get("uri")


def ensure_avatar(session: dict) -> None:
    """Give the account the LungDaily avatar once (keeps name/bio set in the app). Done once, then
    remembered in data/bluesky.json, so an avatar changed later in the app is left alone."""
    from .util import DATA, ROOT
    state = read_json(DATA / "bluesky.json", {})
    if state.get("avatar_set"):
        return
    auth = {"Authorization": f"Bearer {session['accessJwt']}"}
    r = requests.get(f"{PDS}/xrpc/com.atproto.repo.getRecord", headers=auth, timeout=30,
                     params={"repo": session["did"], "collection": "app.bsky.actor.profile", "rkey": "self"})
    rec = r.json().get("value", {}) if r.ok else {}
    img = ROOT / "static" / "icons" / "avatar-400.png"
    if not img.exists():
        return
    up = requests.post(f"{PDS}/xrpc/com.atproto.repo.uploadBlob", data=img.read_bytes(), timeout=60,
                       headers={**auth, "Content-Type": "image/png"})
    if up.status_code >= 400:
        log.warning("Bluesky avatar upload failed: %s", up.text[:200])
        return
    rec = {**rec, "$type": "app.bsky.actor.profile", "avatar": up.json()["blob"]}
    w = requests.post(f"{PDS}/xrpc/com.atproto.repo.putRecord", headers=auth, timeout=30,
                      json={"repo": session["did"], "collection": "app.bsky.actor.profile", "rkey": "self", "record": rec})
    log.info("Bluesky avatar set: %s", w.status_code)
    if w.ok:
        write_json(DATA / "bluesky.json", {**state, "avatar_set": now().isoformat(timespec="seconds")})


def run_pending() -> int:
    from .amplify import HASHTAGS
    from .awareness import hashtags_for
    cfg = settings()
    acfg, base = cfg["amplify"], cfg["site"]["base_url"].rstrip("/")
    if not acfg.get("bluesky_post"):
        return 0
    window = dt.timedelta(hours=float(acfg.get("bluesky_window_hours", 36)))
    todo = []
    for p in POSTS.glob("*.json"):
        rec = read_json(p, {})
        sh = rec.get("shared") or {}
        if (rec.get("status") == "published" and "bluesky" not in sh and rec.get("bluesky_attempts", 0) < 3
                and now() - (parse_date(rec.get("published_at")) or now()) <= window):
            todo.append((p, rec))
    session = _session()
    if not session:
        return 0
    try:
        ensure_avatar(session)
    except Exception as e:
        log.warning("Bluesky avatar check failed: %s", e)
    if not todo:
        return 0
    todo.sort(key=lambda pr: pr[1].get("published_at", ""))
    done = 0
    for p, rec in todo[: int(acfg.get("bluesky_max_per_run", 3))]:
        tags = hashtags_for(rec)[:1]
        for t in rec.get("tags") or []:
            if t in HASHTAGS and HASHTAGS[t] not in tags:
                tags.append(HASHTAGS[t])
        uri = post(rec, f"{base}/news/{rec['slug']}/", tags[: int(acfg.get("bluesky_hashtags", 2))], session)
        if uri:
            rec.setdefault("shared", {})["bluesky"] = uri
            done += 1
            log.info("Bluesky: posted %s", rec["slug"])
        else:
            rec["bluesky_attempts"] = rec.get("bluesky_attempts", 0) + 1
        write_json(p, rec)
    return done
