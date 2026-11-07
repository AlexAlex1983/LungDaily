"""Share new articles on X and Telegram, and send a run report to your private Telegram chat.

X posting uses OAuth 1.0a user context: X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET
(the site's own X account). Telegram: TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL (e.g. @lungdaily),
TELEGRAM_ADMIN_CHAT_ID (your user id, for reports).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import os
import secrets
import time
import urllib.parse

import requests

from .util import DATA, POSTS, log, read_json, settings, write_json

AMPLIFY_QUEUE = DATA / "amplify_queue.json"
HELD = DATA / "held.json"
TWEET_URL = "https://api.x.com/2/tweets"


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _oauth1_header(method: str, url: str) -> str:
    q = urllib.parse.quote
    params = {
        "oauth_consumer_key": _env("X_API_KEY"),
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": _env("X_ACCESS_TOKEN"),
        "oauth_version": "1.0",
    }
    base_params = "&".join(f"{q(k, safe='')}={q(v, safe='')}" for k, v in sorted(params.items()))
    base = "&".join([method.upper(), q(url, safe=""), q(base_params, safe="")])
    key = f"{q(_env("X_API_SECRET"), safe='')}&{q(_env("X_ACCESS_SECRET"), safe='')}"
    sig = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()
    params["oauth_signature"] = sig
    return "OAuth " + ", ".join(f'{q(k, safe="")}="{q(v, safe="")}"' for k, v in sorted(params.items()))


def x_text(rec: dict, url: str, cfg: dict) -> str:
    """Fit headline + link in 280 chars (links count as 23)."""
    head = rec["headline"]
    mention = ""
    if cfg["x_mention_authors"] and rec["source"].get("author_handle"):
        mention = f" @{rec['source']['author_handle']}"
    room = 280 - 23 - 2 - len(mention)
    if len(head) > room:
        head = head[: room - 1].rstrip() + "…"
    return f"{head}{mention}\n\n{url}"


def post_x(rec: dict, url: str, cfg: dict) -> str | None:
    if not all(os.environ.get(k) for k in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")):
        log.info("X posting: credentials missing, skipping")
        return None
    body = {"text": x_text(rec, url, cfg)}
    if cfg["x_quote_source_tweet"] and rec["embed"].get("type") == "x" and rec["embed"].get("tweet_id"):
        body["quote_tweet_id"] = rec["embed"]["tweet_id"]
    r = requests.post(TWEET_URL, json=body, headers={"Authorization": _oauth1_header("POST", TWEET_URL)}, timeout=30)
    if r.status_code == 402:
        log.error("X posting: 402 — credits exhausted")
        return None
    if r.status_code >= 400 and "quote_tweet_id" in body:
        body.pop("quote_tweet_id")  # quoted post may be protected/deleted — retry as a plain post
        r = requests.post(TWEET_URL, json=body, headers={"Authorization": _oauth1_header("POST", TWEET_URL)}, timeout=30)
    if r.status_code >= 400:
        detail = " ".join(r.text.split())[:400]
        hint = (" — check that the Access Token/Secret were generated AFTER setting the app to Read and write, "
                "and that all four X_* secrets come from the same @LungDaily app") if r.status_code in (401, 403) else ""
        log.warning("X posting failed: %s %s%s", r.status_code, detail, hint)
        return None
    return r.json().get("data", {}).get("id")


def tg_send(chat: str, text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token or not chat:
        return False
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={
        "chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": False}, timeout=30)
    if r.status_code >= 400:
        log.warning("Telegram send failed: %s", r.text[:200])
    return r.ok


def write_github_summary(done: list, stats: dict | None, base: str) -> None:
    """Run report shown on the GitHub Actions run page (Summary)."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    held = read_json(HELD, [])
    lines = ["## LungDaily run report", ""]
    if stats:
        lines.append(f"Triaged **{stats.get('triaged', 0)}** · candidates **{stats.get('candidates', 0)}** · "
                     f"published **{stats.get('published', 0)}** · held **{stats.get('held', 0)}**")
        lines.append("")
    if done:
        lines.append("### Published")
        for slug in done:
            rec = read_json(POSTS / f"{slug}.json", None)
            if rec:
                lines.append(f"- [{rec['headline']}]({base}/news/{slug}/)")
        lines.append("")
    if held:
        lines.append("### Held for review")
        lines.append("Release: Actions → Pipeline → Run workflow → `release` + slug. Drop: `discard` + slug.")
        lines.append("")
        for h in held:
            issues = "; ".join(h["checker"]["issues"]) or "awaiting review"
            lines.append(f"- **{h['headline']}**  \n  slug: `{h['slug']}` — {issues}")
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def run(stats: dict | None = None) -> None:
    cfg = settings()
    acfg, base = cfg["amplify"], cfg["site"]["base_url"].rstrip("/")
    queue = read_json(AMPLIFY_QUEUE, [])
    done = []
    for slug in queue:
        path = POSTS / f"{slug}.json"
        rec = read_json(path, None)
        if not rec:
            done.append(slug)
            continue
        url = f"{base}/news/{slug}/"
        shared = rec.setdefault("shared", {})
        retry = False
        if acfg["x_post"] and "x" not in shared:
            tid = post_x(rec, url, acfg)
            if tid:
                shared["x"] = tid
            elif os.environ.get("X_ACCESS_TOKEN"):
                attempts = rec.get("x_attempts", 0) + 1
                rec["x_attempts"] = attempts
                retry = attempts < 4  # try again on the next runs, then give up
        channel = os.environ.get("TELEGRAM_CHANNEL") or cfg["site"].get("telegram_channel")
        if acfg["telegram_post"] and "telegram" not in shared and channel:
            msg = f"<b>{html.escape(rec['headline'])}</b>\n\n{html.escape(rec['dek'])}\n\n{url}"
            if tg_send(channel, msg):
                shared["telegram"] = True
        write_json(path, rec)
        if not retry:
            done.append(slug)
    write_json(AMPLIFY_QUEUE, [s for s in queue if s not in done])

    write_github_summary(done, stats, base)

    if acfg["telegram_admin_report"] and os.environ.get("TELEGRAM_ADMIN_CHAT_ID"):
        held = read_json(HELD, [])
        lines = ["<b>LungDaily — run report</b>"]
        if stats:
            lines.append(f"Triaged {stats.get('triaged', 0)} · candidates {stats.get('candidates', 0)} · "
                         f"published {stats.get('published', 0)} · held {stats.get('held', 0)}")
        for slug in done:
            rec = read_json(POSTS / f"{slug}.json", None)
            if rec:
                lines.append(f"✅ <a href=\"{base}/news/{slug}/\">{html.escape(rec['headline'])}</a>")
        for h in held[-10:]:
            issues = "; ".join(h["checker"]["issues"])[:200] or "awaiting review"
            lines.append(f"⏸ {html.escape(h['headline'])}\n   <code>{h['slug']}</code> — {html.escape(issues)}")
        if held:
            lines.append("Release a held article: Actions → Pipeline → Run workflow → release = slug")
        if len(lines) > 1 and (done or held or stats):
            tg_send(os.environ["TELEGRAM_ADMIN_CHAT_ID"], "\n".join(lines)[:4000])
