"""Grow @LungDaily's network: follow relevant respiratory accounts via the X API.

Candidates come for free from the X searches the pipeline already runs (no extra reads):
authors whose bio shows they work in respiratory / lung health, plus everyone in voices.yaml.
Every run follows at most `follow.per_run` accounts, never the same account twice, never
anyone in `follow.exclude` or the opt-out list.

Needs the same OAuth 1.0a keys as posting (X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET)
and X_BEARER_TOKEN for username lookups.
"""
from __future__ import annotations

import os
import re

import requests

from .amplify import _env, _oauth1_header
from .util import DATA, load_yaml, log, now, read_json, settings, write_json

CANDIDATES = DATA / "follow_candidates.json"
STATE = DATA / "follow_state.json"
API = "https://api.x.com/2"

RELEVANT_BIO = re.compile(
    r"pulmonolog|pneumolog|respirolog|respiratory|thoracic|chest physician|lung|copd|asthma|"
    r"bronchiectasis|pulmonary|\bild\b|\bipf\b|tuberculosis|\btb\b|spirometr|airway|"
    r"pneumolog[ií]a|neumolog|pneumologie|pulmolog|pulmonolog",
    re.I,
)


def note_candidates(users: list[dict]) -> None:
    """Called by the X collector with the author objects it already received."""
    cfg = settings().get("follow", {})
    min_followers = int(cfg.get("min_followers", 300))
    cand = read_json(CANDIDATES, {})
    for u in users:
        bio = (u.get("description") or "") + " " + (u.get("name") or "")
        followers = (u.get("public_metrics") or {}).get("followers_count", 0)
        if not u.get("username") or followers < min_followers or not RELEVANT_BIO.search(bio):
            continue
        e = cand.setdefault(u["username"].lower(), {"id": u["id"], "username": u["username"], "seen": 0})
        e.update({"name": u.get("name"), "bio": (u.get("description") or "")[:200], "followers": followers})
        e["seen"] += 1
    write_json(CANDIDATES, cand)


def _creds_ok() -> bool:
    return all(_env(k) for k in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET"))


def _me(state: dict) -> str | None:
    if state.get("me_id"):
        return state["me_id"]
    url = f"{API}/users/me"
    r = requests.get(url, headers={"Authorization": _oauth1_header("GET", url)}, timeout=30)
    if r.ok:
        state["me_id"] = r.json()["data"]["id"]
        state["me_username"] = r.json()["data"]["username"]
        return state["me_id"]
    log.warning("follow: could not read own account: %s %s", r.status_code, r.text[:200])
    return None


def _lookup_id(username: str) -> str | None:
    token = (os.environ.get("X_BEARER_TOKEN") or "").strip()
    if not token:
        return None
    r = requests.get(f"{API}/users/by/username/{username}", headers={"Authorization": f"Bearer {token}"}, timeout=30)
    return r.json().get("data", {}).get("id") if r.ok else None


def _follow(me: str, target_id: str) -> tuple[bool, str]:
    url = f"{API}/users/{me}/following"
    r = requests.post(url, json={"target_user_id": target_id},
                      headers={"Authorization": _oauth1_header("POST", url)}, timeout=30)
    return r.ok, f"{r.status_code} {' '.join(r.text.split())[:200]}"


def _unfollow(me: str, target_id: str) -> tuple[bool, str]:
    url = f"{API}/users/{me}/following/{target_id}"
    r = requests.delete(url, headers={"Authorization": _oauth1_header("DELETE", url)}, timeout=30)
    return r.ok, f"{r.status_code} {' '.join(r.text.split())[:200]}"


def pick(state: dict, cfg: dict) -> list[dict]:
    """Order: voices.yaml first, then suggested voices, then other relevant authors by audience."""
    exclude = {h.lower().lstrip("@") for h in (cfg.get("exclude") or [])}
    exclude |= {h.lower().lstrip("@") for h in (load_yaml("optout.yaml").get("x_handles") or [])}
    done = {h.lower() for h in state.get("followed", {})} | {h.lower() for h in state.get("failed", {})}
    if state.get("me_username"):
        exclude.add(state["me_username"].lower())
    out, seen = [], set()

    def add(username, uid=None, why=""):
        k = (username or "").lower()
        if not k or k in exclude or k in done or k in seen:
            return
        seen.add(k)
        out.append({"username": username, "id": uid, "why": why})

    for v in load_yaml("voices.yaml").get("voices", []):
        if v.get("x"):
            add(v["x"], why="followed voice")
    cand = read_json(CANDIDATES, {})
    sug = read_json(DATA / "suggested_voices.json", {})
    for e in sorted(sug.values(), key=lambda e: -e["hits"]):
        c = cand.get(e["handle"].lower())
        if c:
            add(c["username"], c["id"], "strong posts")
    for c in sorted(cand.values(), key=lambda c: (-c.get("seen", 0), -c.get("followers", 0))):
        add(c["username"], c["id"], "respiratory bio")
    return out


def run() -> dict:
    cfg = settings().get("follow", {})
    stats = {"followed": [], "unfollowed": [], "errors": []}
    if not cfg.get("enabled"):
        log.info("follow: disabled in settings")
        return stats
    if not _creds_ok():
        log.info("follow: X posting credentials missing, skipping")
        return stats
    state = read_json(STATE, {"followed": {}, "failed": {}, "unfollowed": []})
    me = _me(state)
    if not me:
        write_json(STATE, state)
        return stats

    # one-off clean-ups listed in settings (e.g. accounts followed by mistake)
    for h in cfg.get("unfollow") or []:
        h = h.lstrip("@")
        if h.lower() in {x.lower() for x in state["unfollowed"]}:
            continue
        uid = _lookup_id(h)
        if uid:
            ok, msg = _unfollow(me, uid)
            if ok:
                state["unfollowed"].append(h)
                state["followed"].pop(h, None)
                stats["unfollowed"].append(h)
            else:
                stats["errors"].append(f"unfollow @{h}: {msg}")

    budget = int(cfg.get("per_run", 10))
    for c in pick(state, cfg):
        if budget <= 0:
            break
        uid = c["id"] or _lookup_id(c["username"])
        if not uid:
            state["failed"][c["username"]] = "user not found"
            continue
        ok, msg = _follow(me, uid)
        if ok:
            state["followed"][c["username"]] = {"at": now().isoformat(timespec="seconds"), "why": c["why"]}
            stats["followed"].append(f"@{c['username']} ({c['why']})")
            budget -= 1
        elif msg.startswith("429") or msg.startswith("402"):
            stats["errors"].append(f"stopped: {msg}")
            break
        else:
            state["failed"][c["username"]] = msg
            stats["errors"].append(f"follow @{c['username']}: {msg}")
    write_json(STATE, state)
    log.info("follow: %s", stats)

    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        lines = ["## LungDaily follow run", ""]
        lines += [f"- Followed {x}" for x in stats["followed"]] or ["- Nobody new to follow this run"]
        lines += [f"- Unfollowed @{x}" for x in stats["unfollowed"]]
        lines += [f"- ⚠️ {x}" for x in stats["errors"]]
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    return stats
