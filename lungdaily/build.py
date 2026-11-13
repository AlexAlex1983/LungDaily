"""Render the static site into ./site (deployed to GitHub Pages)."""
from __future__ import annotations

import html
import json
import shutil
from xml.sax.saxutils import escape

import markdown
from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import images
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
           "today": f"{now().strftime('%A')}, {now().day} {now().strftime('%B %Y')}"}

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
        related = [r for r in posts if r is not p and set(r["tags"]) & set(p["tags"])][:4]
        same_cat = [r for r in posts if r is not p and r["category"] == p["category"] and r not in related][:3]
        _write(OUT / "news" / p["slug"] / "index.html", env.get_template("article.html").render(
            **ctx, root="../../", post=p, related=related, more=same_cat,
            latest=[r for r in posts[:9] if r is not p][:8], title=f"{p['headline']} — {site['name']}", active=p["category"]))
    for page in ("about", "editorial-policy", "opt-out"):
        _write(OUT / page / "index.html", env.get_template(f"{page}.html").render(**ctx, root="../", title=f"{page.replace('-', ' ').title()} — {site['name']}"))

    _write(OUT / "rss.xml", rss(posts[:50], site, base))
    _write(OUT / "sitemap.xml", sitemap(posts, base))
    _write(OUT / "news-sitemap.xml", news_sitemap(posts, site, base))
    _write(OUT / "robots.txt", f"User-agent: *\nAllow: /\n\nSitemap: {base}/sitemap.xml\nSitemap: {base}/news-sitemap.xml\n")
    _write(OUT / "site.webmanifest", json.dumps({
        "name": site["name"], "short_name": site["name"], "description": site.get("description", ""),
        "start_url": "/", "display": "browser", "theme_color": "#0B6E73", "background_color": "#ffffff",
        "icons": [{"src": f"/static/icons/icon-{s}.png", "sizes": f"{s}x{s}", "type": "image/png"} for s in (192, 512)]}, indent=1))
    log.info("Built site: %d articles -> %s", len(posts), OUT)
    return len(posts)


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


def sitemap(posts, base) -> str:
    urls = [f"{base}/"] + [f"{base}/{c}/" for c in CATEGORY_LABELS] + [f"{base}/news/{p['slug']}/" for p in posts]
    body = "".join(f"<url><loc>{u}</loc></url>" for u in urls)
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
