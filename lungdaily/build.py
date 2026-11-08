"""Render the static site into ./site (deployed to GitHub Pages)."""
from __future__ import annotations

import html
import shutil
from xml.sax.saxutils import escape

import markdown
from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import images
from .util import POSTS, ROOT, iso, load_yaml, log, now, parse_date, read_json, settings

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
        emb = rec.get("embed") or {}
        rec["x_embed_url"] = emb.get("url") if emb.get("type") == "x" else None
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
    if site.get("custom_domain"):
        (OUT / "CNAME").write_text(site["custom_domain"] + "\n")

    env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape(["html"]))
    env.filters["datefmt"] = lambda d: d.strftime("%d %b %Y, %H:%M UTC") if d else ""
    env.filters["isodate"] = iso
    ctx = {"site": site, "base": base, "categories": CATEGORY_LABELS, "year": now().year,
           "build_id": now().strftime("%Y%m%d%H%M")}

    _write(OUT / "index.html", env.get_template("index.html").render(
        **ctx, root="./", posts=posts[:40], title=f"{site['name']}: {site['tagline']}", heading=None))
    for cat, label in CATEGORY_LABELS.items():
        sel = [p for p in posts if p["category"] == cat]
        _write(OUT / cat / "index.html", env.get_template("index.html").render(
            **ctx, root="../", posts=sel[:60], title=f"{label} — {site['name']}", heading=label, active=cat))
    for p in posts:
        try:
            pic = images.custom_image(p["slug"])
            if pic:
                images.render_custom(p, pic, OUT / "cards" / f"{p['slug']}.png", p["category_label"])
                images.render_custom(p, pic, OUT / "thumbs" / f"{p['slug']}.png", p["category_label"], variant="thumb")
            else:
                images.render(p, OUT / "cards" / f"{p['slug']}.png", p["category_label"])
                images.render(p, OUT / "thumbs" / f"{p['slug']}.png", p["category_label"], variant="thumb")
        except Exception as e:  # a broken image must never stop the site build
            log.warning("Card image failed for %s: %s", p["slug"], e)
        related = [r for r in posts if r is not p and set(r["tags"]) & set(p["tags"])][:4]
        _write(OUT / "news" / p["slug"] / "index.html", env.get_template("article.html").render(
            **ctx, root="../../", post=p, related=related, title=f"{p['headline']} — {site['name']}", active=p["category"]))
    for page in ("about", "editorial-policy", "opt-out"):
        _write(OUT / page / "index.html", env.get_template(f"{page}.html").render(**ctx, root="../", title=f"{page.replace('-', ' ').title()} — {site['name']}"))

    _write(OUT / "rss.xml", rss(posts[:50], site, base))
    _write(OUT / "sitemap.xml", sitemap(posts, base))
    _write(OUT / "news-sitemap.xml", news_sitemap(posts, site, base))
    log.info("Built site: %d articles -> %s", len(posts), OUT)
    return len(posts)


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
