"""Render the static site into ./site (deployed to GitHub Pages)."""
from __future__ import annotations

import html
import json
import shutil
from xml.sax.saxutils import escape

import markdown
from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import awareness, images
from .util import POSTS, ROOT, iso, load_yaml, log, now, parse_date, read_json, settings, write_json

OUT = ROOT / "site"

CATEGORY_LABELS = {
    "voices": "Voices", "research": "New Paper Alert", "policy": "Policy & Air",
    "approvals": "Approvals & Trials", "people": "People", "events": "Events",
}


def load_posts() -> list[dict]:
    takedown = set(load_yaml("optout.yaml").get("takedown") or [])
    posts = []
    for p in POSTS.glob("*.json"):
        rec = read_json(p, {})
        if rec.get("status") != "published" or rec["slug"] in takedown:
            continue
        rec["date"] = parse_date(rec["published_at"])
        rec["html"] = markdown.markdown(html.escape(rec["body"], quote=False))
        rec["category_label"] = CATEGORY_LABELS.get(rec["category"], rec["category"].title())
        rec["has_picture"] = images.custom_image(rec["slug"]) is not None
        rec["picture_credit"] = images.custom_credit(rec["slug"]) if rec["has_picture"] else ""
        emb = rec.get("embed") or {}
        rec["x_embed_url"] = emb.get("url") if emb.get("type") == "x" else None
        rec["read_min"] = max(1, round(len(rec["body"].split()) / 200))
        posts.append(rec)
    posts.sort(key=lambda r: r["date"], reverse=True)
    return posts


def _write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build() -> int:
    cfg = settings()
    site = cfg["site"]
    base = site["base_url"].rstrip("/")
    posts = load_posts()

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    shutil.copytree(ROOT / "static", OUT / "static")
    if (ROOT / "content" / "figures").exists():
        shutil.copytree(ROOT / "content" / "figures", OUT / "figures")
    (OUT / ".nojekyll").write_text("")
    shutil.copy(ROOT / "static" / "favicon.ico", OUT / "favicon.ico")  # search engines look for /favicon.ico
    if site.get("custom_domain"):
        (OUT / "CNAME").write_text(site["custom_domain"] + "\n")

    env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape(["html"]))
    env.filters["datefmt"] = lambda d: d.strftime("%d %b %Y, %H:%M UTC") if d else ""
    env.filters["shortdate"] = lambda d: f"{d.day} {d.strftime('%b %Y')}" if d else ""
    env.filters["isodate"] = iso
    ctx = {"site": site, "base": base, "categories": CATEGORY_LABELS, "year": now().year,
           "build_id": now().strftime("%Y%m%d%H%M"),
           "today": f"{now().strftime('%A')}, {now().day} {now().strftime('%B %Y')}",
           "awareness_banner": awareness.active(), "newsletter": cfg.get("newsletter") or {}}

    _write(OUT / "index.html", env.get_template("index.html").render(
        **ctx, root="./", posts=posts[:40], front=front_page(posts),
        title=f"{site['name']}: {site['tagline']}", heading=None))
    for cat, label in CATEGORY_LABELS.items():
        sel = [p for p in posts if p["category"] == cat]
        _write(OUT / cat / "index.html", env.get_template("index.html").render(
            **ctx, root="../", posts=sel[:60], title=f"{label} — {site['name']}", heading=label, active=cat))
    pics_cfg = cfg.get("pictures") or {}
    lib_index = images.library_index() if pics_cfg.get("library", True) else {}
    lib_state = read_json(images.LIBRARY_STATE, {})
    # assign library pictures oldest-first so the rotation is stable across rebuilds
    lib_pics = {}
    for p in sorted(posts, key=lambda r: r["date"]):
        if not p["has_picture"]:
            try:
                lib_pics[p["slug"]] = images.library_picture(p, lib_state, lib_index, float(pics_cfg.get("library_share", 1.0)))
            except Exception as e:
                log.warning("Library picture failed for %s: %s", p["slug"], e)
    if lib_index:
        write_json(images.LIBRARY_STATE, lib_state)
    import datetime as dt
    featured_window = dt.timedelta(days=14)
    for p in posts:
        try:
            pic = images.custom_image(p["slug"]) or lib_pics.get(p["slug"])
            if pic:
                images.render_custom(p, pic, OUT / "cards" / f"{p['slug']}.png", p["category_label"])
                images.render_custom(p, pic, OUT / "thumbs" / f"{p['slug']}.png", p["category_label"], variant="thumb")
            else:
                images.render(p, OUT / "cards" / f"{p['slug']}.png", p["category_label"])
                images.render(p, OUT / "thumbs" / f"{p['slug']}.png", p["category_label"], variant="thumb")
        except Exception as e:  # a broken image must never stop the site build
            log.warning("Card image failed for %s: %s", p["slug"], e)
        if now() - p["date"] <= featured_window:
            try:   # "Featured in LungDaily" version of the card, for the people/orgs we wrote about
                images.render_featured(OUT / "cards" / f"{p['slug']}.png", OUT / "cards" / "featured" / f"{p['slug']}.png")
            except Exception as e:
                log.warning("Featured card failed for %s: %s", p["slug"], e)
        related = [r for r in posts if r is not p and set(r["tags"]) & set(p["tags"])][:4]
        same_cat = [r for r in posts if r is not p and r["category"] == p["category"] and r not in related][:3]
        _write(OUT / "news" / p["slug"] / "index.html", env.get_template("article.html").render(
            **ctx, root="../../", post=p, related=related, more=same_cat,
            latest=[r for r in posts[:9] if r is not p][:8], title=f"{p['headline']} — {site['name']}", active=p["category"]))
    page_titles = {"submit-news": "Send us your news", "featured": "Featured in LungDaily",
                   "newsletter": "The Week in Respiratory Health — newsletter"}
    for page in ("about", "editorial-policy", "opt-out", "submit-news", "featured", "newsletter"):
        _write(OUT / page / "index.html", env.get_template(f"{page}.html").render(
            **ctx, root="../", title=f"{page_titles.get(page, page.replace('-', ' ').title())} — {site['name']}"))

    images.render_badge(OUT / "badges" / "featured-in-lungdaily.png")
    shutil.copy(ROOT / "static" / "featured-badge.svg", OUT / "badges" / "featured-in-lungdaily.svg")

    day_slugs, day_list = [], []
    for day, sel in awareness_pages(posts):
        day_slugs.append(day["slug"])
        until = day.get("until")
        when = f"{day['date'].strftime('%A')} {day['date'].day} {day['date'].strftime('%B %Y')}"
        if until:
            when = (f"{day['date'].day} {day['date'].strftime('%B')} to {until.day} "
                    f"{until.strftime('%B %Y')}" + (f" in {day['city']}" if day.get("city") else ""))
        day["when_label"] = (f"{day['date'].day} {day['date'].strftime('%B %Y')}" if not until
                             else f"{day['date'].day}–{until.day} {until.strftime('%B %Y')}")
        upcoming = (until or day["date"]) >= now().date()
        intro = (f"{day['name']} {'runs' if until else 'is on'} {when}" if upcoming
                 else f"{day['name']} {'ran' if until else 'was on'} {when}")
        intro = (intro + (f", led by {day['organiser']}" if day.get("organiser") else "")
                 + f". Our latest coverage{' · ' + day['hashtag'] if day.get('hashtag') else ''}.")
        togo = (day["date"] - now().date()).days
        countdown = ("today" if day["date"] <= now().date() <= (until or day["date"])
                     else (f"in {togo} day{'s' if togo != 1 else ''}" if 0 < togo <= 60 else ""))
        heading = day["name"] if str(day['date'].year) in day["name"] else f"{day['name']} {day['date'].year}"
        page = f"{base}/days/{day['slug']}/"
        kit_text = (f"{heading} — {day['when_label']}" + (f": “{day['theme']}”" if day.get("theme") else "") + ".\n\n"
                    + (f"{day['facts'][0]['figure']} {day['facts'][0]['text']} ({day['facts'][0]['source']}).\n\n" if day.get("facts") else "")
                    + f"News, facts and expert voices in one place: {page}?utm_source=sharekit&utm_campaign={day['slug']}\n\n"
                    + " ".join(h for h in [day.get("hashtag"), "#LungHealth"] if h))
        kit_x = f"{heading}" + (f": “{day['theme']}”" if day.get("theme") else "") + " — news and facts via @" + site.get("x_handle", "LungDaily")
        if day.get("hashtag"):
            kit_x += f" {day['hashtag']}"
        orgs = coverage_orgs(sel)
        try:
            images.render_day(dict(day, name=heading), OUT / "cards" / "days" / f"{day['slug']}.png")
        except Exception as e:
            log.warning("Day card failed for %s: %s", day["slug"], e)
        _write(OUT / "days" / day["slug"] / "index.html", env.get_template("day.html").render(
            **ctx, root="../../", day=day, posts=sel[:60], title=f"{heading} — {site['name']}", heading=heading,
            intro=intro, when_label=day["when_label"], countdown=countdown, kit_text=kit_text, kit_x=kit_x,
            orgs=orgs, active="days"))
        day_list.append(day)
    day_list.sort(key=lambda d: (d["date"] < now().date() - dt.timedelta(days=7), d["date"]))
    _write(OUT / "days" / "index.html", env.get_template("days.html").render(
        **ctx, root="../", days=day_list, title=f"Awareness days & congresses — {site['name']}", active="days"))

    from .reports import build_reports
    reps = build_reports(posts)
    for r in reps:
        _write(OUT / "reports" / r["key"] / "index.html", env.get_template("report.html").render(
            **ctx, root="../../", r=r, title=f"{r['title']} — {site['name']}"))
    _write(OUT / "reports" / "index.html", env.get_template("reports.html").render(
        **ctx, root="../", reports=reps, title=f"Monthly reports — {site['name']}"))

    live = {p["slug"] for p in posts}
    for old, new in (load_yaml("optout.yaml").get("redirects") or {}).items():
        if new in live and old not in live:   # taken-down duplicates / renamed slugs -> the live article
            _write(OUT / "news" / old / "index.html", redirect_page(f"{base}/news/{new}/"))
        else:
            log.warning("Redirect %s -> %s skipped (target not live, or old slug still live)", old, new)

    _write(OUT / "rss.xml", rss(posts[:50], site, base))
    _write(OUT / "sitemap.xml", sitemap(posts, base, day_slugs, [r["key"] for r in reps]))
    _write(OUT / "news-sitemap.xml", news_sitemap(posts, site, base))
    _write(OUT / "robots.txt", f"User-agent: *\nAllow: /\n\nSitemap: {base}/sitemap.xml\nSitemap: {base}/news-sitemap.xml\n")
    _write(OUT / "site.webmanifest", json.dumps({
        "name": site["name"], "short_name": site["name"], "description": site.get("description", ""),
        "start_url": "/", "display": "browser", "theme_color": "#0B6E73", "background_color": "#ffffff",
        "icons": [{"src": f"/static/icons/icon-{s}.png", "sizes": f"{s}x{s}", "type": "image/png"} for s in (192, 512)]}, indent=1))
    log.info("Built site: %d articles -> %s", len(posts), OUT)
    return len(posts)


def redirect_page(url: str) -> str:
    """GitHub Pages can't send 301s; an instant meta refresh + canonical is treated by Google as a permanent redirect."""
    u = html.escape(url)
    return (f'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Moved</title>'
            f'<link rel="canonical" href="{u}">'
            f'<meta http-equiv="refresh" content="0; url={u}"></head>'
            f'<body><p>This article has moved: <a href="{u}">{u}</a></p></body></html>\n')


def front_page(posts: list[dict]) -> dict:
    """Split the newest posts into a newspaper-style front page.

    lead = newest article; secondary = next 3; latest = headline list; sections = up to
    4 per category that are not already shown above, in CATEGORY_LABELS order.
    """
    lead, secondary = (posts[:1] or [None])[0], posts[1:4]
    shown = {p["slug"] for p in posts[:4]}
    latest = posts[4:12]
    sections = []
    for cat, label in CATEGORY_LABELS.items():
        sel = [p for p in posts if p["category"] == cat and p["slug"] not in shown][:4]
        if sel:
            sections.append({"key": cat, "label": label, "posts": sel})
            shown.update(p["slug"] for p in sel)
    return {"lead": lead, "secondary": secondary, "latest": latest, "sections": sections}


def rss(posts, site, base) -> str:
    items = "".join(
        f"<item><title>{escape(p['headline'])}</title><link>{base}/news/{p['slug']}/</link>"
        f"<guid isPermaLink=\"true\">{base}/news/{p['slug']}/</guid>"
        f"<pubDate>{p['date'].strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate>"
        f"<category>{escape(p['category_label'])}</category>"
        f"<description>{escape(p['dek'])}</description></item>"
        for p in posts)
    return ('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
            f"<title>{escape(site['name'])}</title><link>{base}/</link>"
            f"<description>{escape(site.get('description') or site['tagline'])}</description><language>en</language>{items}</channel></rss>")


def coverage_orgs(sel: list[dict], limit: int = 16) -> list[str]:
    """Tracked voices (voices.yaml) whose posts/news are in the coverage; never the site's own people
    (settings follow.exclude)."""
    from .featured import source_voice
    skip = {h.lower() for h in (settings().get("follow") or {}).get("exclude") or []}
    out = []
    for p in sel:
        v = source_voice(p)
        if not v or (v.get("x") or "").lower() in skip:
            continue
        if v["name"] not in out:
            out.append(v["name"])
    return out[:limit]


def awareness_pages(posts: list[dict]) -> list[tuple[dict, list[dict]]]:
    """(day, matching posts) for each awareness day / congress: the occurrence that is current or
    next (within a year), else the most recent one that has coverage. One page per slug.

    Coverage = articles about the day published from 14 days before to 7 days after it."""
    import datetime as dt
    today = now().date()
    best: dict = {}
    for year in (today.year - 1, today.year, today.year + 1):
        for day in awareness.all_days(year):
            end = day.get("until") or day["date"]
            lo, hi = day["date"] - dt.timedelta(days=14), end + dt.timedelta(days=7)
            sel = [p for p in posts if lo <= p["date"].date() <= hi and awareness.matches(day, awareness.item_text(p))]
            current = end >= today - dt.timedelta(days=7) and day["date"] <= today + dt.timedelta(days=366)
            if not (current or sel):
                continue
            rank = (0, day["date"]) if current else (1, -day["date"].toordinal())   # nearest upcoming first
            if day["slug"] not in best or rank < best[day["slug"]][0]:
                best[day["slug"]] = (rank, day, sel)
    return [(d, sel) for _, d, sel in sorted(best.values(), key=lambda t: t[0])]


def sitemap(posts, base, day_slugs=(), report_keys=()) -> str:
    pages = [(f"{base}/", None)] + [(f"{base}/{c}/", None) for c in CATEGORY_LABELS]
    pages += [(f"{base}/days/{s}/", None) for s in day_slugs]
    pages += [(f"{base}/days/", None), (f"{base}/reports/", None)] + [(f"{base}/reports/{k}/", None) for k in report_keys]
    pages += [(f"{base}/{pg}/", None) for pg in ("about", "editorial-policy", "opt-out", "submit-news", "newsletter", "featured")]
    pages += [(f"{base}/news/{p['slug']}/", p["date"]) for p in posts]
    if posts:
        pages[0] = (pages[0][0], posts[0]["date"])
    body = "".join(f"<url><loc>{u}</loc>" + (f"<lastmod>{iso(d)}</lastmod>" if d else "") + "</url>" for u, d in pages)
    return f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>'


def news_sitemap(posts, site, base) -> str:
    import datetime as dt
    cutoff = now() - dt.timedelta(days=2)
    body = "".join(
        f"<url><loc>{base}/news/{p['slug']}/</loc><news:news><news:publication><news:name>{escape(site['name'])}"
        f"</news:name><news:language>en</news:language></news:publication>"
        f"<news:publication_date>{iso(p['date'])}</news:publication_date><news:title>{escape(p['headline'])}"
        f"</news:title></news:news></url>"
        for p in posts if p["date"] >= cutoff)
    return ('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
            f'xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">{body}</urlset>')
