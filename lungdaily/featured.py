"""'You've been featured' notes to an organisation's press office.

After deploy, each article published in the last `window_hours` whose SOURCE is an organisation
in voices.yaml with a `press_email` gets one short note: the link, the share card and one-click
share links. At most one note per address every `per_address_days` days (default 7), never to
opted-out addresses or organisations.

settings.yaml -> featured_email.send: false = preview only (the notes are listed in the run report).
Sending uses Brevo (settings smtp_host/smtp_port) with the SMTP_USER / SMTP_PASSWORD secrets.
"""
from __future__ import annotations

import datetime as dt
import os
import smtplib
import ssl
import urllib.parse
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from .util import DATA, POSTS, load_yaml, log, now, parse_date, read_json, settings, write_json

STATE = DATA / "featured.json"

SOURCE_WORDS = {"x": "post on X", "linkedin": "LinkedIn post", "pubmed": "paper", "rss": "news release"}


def source_voice(rec: dict) -> dict | None:
    """The voices.yaml organisation this article is based on (matched by X handle, LinkedIn URL or name)."""
    src = rec.get("source") or {}
    handle = (src.get("author_handle") or "").lower().lstrip("@")
    author = (src.get("author_name") or "").strip().lower()
    url = (src.get("author_url") or "").lower().rstrip("/")
    for v in load_yaml("voices.yaml").get("voices", []):
        names = [n.lower() for n in [v.get("name", ""), v.get("linkedin_name", "")] + list(v.get("aliases") or []) if n]
        if ((handle and handle == (v.get("x") or "").lower())
                or (url and url == (v.get("linkedin") or "").lower().rstrip("/"))
                or (author and author in names)):
            return v
    return None


def blocked(v: dict, email: str) -> bool:
    opt = load_yaml("optout.yaml")
    emails = {e.lower() for e in opt.get("emails") or []}
    names = {n.lower() for n in opt.get("names") or []}
    handles = {h.lower().lstrip("@") for h in opt.get("x_handles") or []}
    return (email.lower() in emails or v.get("name", "").lower() in names
            or (v.get("x") or "").lower() in handles)


def compose(rec: dict, v: dict, base: str, site: dict) -> tuple[str, str, str]:
    """(subject, plain text, html) — short, factual, no hard sell."""
    url = f"{base}/news/{rec['slug']}/"
    card = f"{base}/cards/{rec['slug']}.png"
    kind = SOURCE_WORDS.get((rec.get("source") or {}).get("source"), "update")
    q = urllib.parse.quote
    x_share = f"https://x.com/intent/post?text={q(rec['headline'])}&url={q(url)}&via={site.get('x_handle', 'LungDaily')}"
    li_share = f"https://www.linkedin.com/sharing/share-offsite/?url={q(url)}"
    name = v.get("linkedin_name") or v["name"]
    subject = f"{site['name']} featured your {kind}: {rec['headline']}"[:150]
    text = f"""Hello {name} team,

{site['name']} ({base.replace('https://', '')}), a daily news site on respiratory health, has published a short article based on your recent {kind}:

{rec['headline']}
{url}

You are welcome to share it. A ready-made image card is here: {card}
Share on X: {x_share}
Share on LinkedIn: {li_share}

If anything in the article needs correcting, just reply to this email and we will fix it promptly.

We send at most one note like this a week. Reply "stop" and we will not send more.

{site['name']} — {site['tagline']}
{base.replace('https://', '')} · @{site.get('x_handle', 'LungDaily')}
"""
    esc = lambda s: (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    html = f"""<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.5;color:#10242b;max-width:600px">
<p>Hello {esc(name)} team,</p>
<p><a href="{base}">{esc(site['name'])}</a>, a daily news site on respiratory health, has published a short article based on your recent {kind}:</p>
<p><a href="{url}"><img src="{card}" alt="{esc(rec['headline'])}" width="560" style="max-width:100%;border:0;border-radius:6px"></a></p>
<p style="font-size:17px"><strong><a href="{url}" style="color:#0b6e73">{esc(rec['headline'])}</a></strong></p>
<p>You are welcome to share it: <a href="{x_share}">share on X</a> · <a href="{li_share}">share on LinkedIn</a> · <a href="{card}">download the image card</a></p>
<p>If anything in the article needs correcting, just reply to this email and we will fix it promptly.</p>
<p style="color:#5d7178;font-size:13px">We send at most one note like this a week. Reply "stop" and we will not send more.<br>
{esc(site['name'])} — {esc(site['tagline'])} · <a href="{base}">{base.replace('https://', '')}</a></p>
</div>"""
    return subject, text, html


def smtp_ready(cfg: dict | None = None) -> bool:
    cfg = cfg or {}
    return bool((os.environ.get("SMTP_HOST") or cfg.get("smtp_host"))
                and os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASSWORD"))


def send(to: str, subject: str, text: str, html: str, cfg: dict) -> bool:
    msg = EmailMessage()
    msg["From"] = formataddr((cfg.get("from_name", "LungDaily"), cfg.get("from_address", "editor@lungdaily.org")))
    msg["To"] = to
    msg["Subject"] = subject
    msg["Reply-To"] = cfg.get("reply_to") or cfg.get("from_address", "editor@lungdaily.org")
    msg["Message-ID"] = make_msgid(domain="lungdaily.org")
    if cfg.get("bcc"):
        msg["Bcc"] = cfg["bcc"]
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    host = os.environ.get("SMTP_HOST") or cfg.get("smtp_host")
    port = int(os.environ.get("SMTP_PORT") or cfg.get("smtp_port") or 587)
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=30)
        else:
            server = smtplib.SMTP(host, port, timeout=30)
            server.starttls(context=ssl.create_default_context())
        with server:
            server.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
            server.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError) as e:
        log.warning("Featured email to %s failed: %s", to, e)
        return False


def run() -> list[dict]:
    """Returns the notes sent or previewed in this run: [{to, org, slug, subject, sent}]."""
    cfg_all = settings()
    cfg = cfg_all.get("featured_email") or {}
    if not cfg.get("enabled"):
        return []
    site, base = cfg_all["site"], cfg_all["site"]["base_url"].rstrip("/")
    live = bool(cfg.get("send")) and smtp_ready(cfg)
    if cfg.get("send") and not live:
        log.info("Featured emails: send is on but SMTP secrets are missing — preview only")
    gap = dt.timedelta(days=float(cfg.get("per_address_days", 7)))
    window = dt.timedelta(hours=float(cfg.get("window_hours", 36)))
    state = read_json(STATE, {})
    out = []
    recs = []
    for p in POSTS.glob("*.json"):
        rec = read_json(p, {})
        if rec.get("status") == "published" and now() - (parse_date(rec.get("published_at")) or now()) <= window:
            recs.append((p, rec))
    recs.sort(key=lambda pr: -float(pr[1].get("score") or 0))   # best article first if an org has two
    for path, rec in recs:
        shared = rec.setdefault("shared", {})
        if "featured_email" in shared or (not live and "featured_preview" in shared):
            continue
        v = source_voice(rec)
        email = (v or {}).get("press_email")
        if not v or not email or blocked(v, email):
            continue
        last = parse_date(state.get(email.lower()))
        if last and now() - last < gap:
            continue
        subject, text, html = compose(rec, v, base, site)
        entry = {"to": email, "org": v["name"], "slug": rec["slug"], "subject": subject, "sent": False}
        if live:
            if send(email, subject, text, html, cfg):
                entry["sent"] = True
                shared["featured_email"] = {"to": email, "at": now().isoformat(timespec="seconds")}
                state[email.lower()] = now().isoformat(timespec="seconds")
                log.info("Featured email sent to %s (%s)", email, rec["slug"])
        else:
            shared["featured_preview"] = {"to": email, "at": now().isoformat(timespec="seconds")}
            state[email.lower()] = now().isoformat(timespec="seconds")   # keep previews to the same weekly rhythm
            log.info("Featured email (preview) to %s: %s", email, subject)
        write_json(path, rec)
        out.append(entry)
    write_json(STATE, state)
    return out


def summary(entries: list[dict]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path or not entries:
        return
    lines = ["", "### \"You've been featured\" emails", ""]
    for e in entries:
        lines.append(f"- {'Sent' if e['sent'] else 'Preview (not sent)'} → {e['org']} <{e['to']}>: {e['subject']}")
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
