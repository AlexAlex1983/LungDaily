"""Offline end-to-end test: fixtures -> collect -> process (fake LLM) -> build -> checks.

Runs on a temporary copy of the project, so real data/ and content/ are untouched.
  python tests/test_offline.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent

SCRIPT = r'''
import json
from pathlib import Path
from lungdaily import collect, process, build, amplify
from lungdaily.sources import linkedin, pubmed, rss
from lungdaily.sources import make_item
from lungdaily.util import POSTS, DATA, read_json

fx = Path("tests/fixtures")
items = [
    make_item("x", "1850000000000000001", url="https://x.com/ERSpublications/status/1850000000000000001",
              text="New ERS statement on COPD case-finding in primary care: spirometry for symptomatic adults over 40 should be routine, say the authors.",
              author_name="European Respiratory Society", author_handle="ERSpublications",
              published_at="2026-11-07T08:00:00+00:00", extra={"tweet_id": "1850000000000000001"}),
    make_item("x", "1850000000000000002", url="https://x.com/blocked/status/2", text="opted out person",
              author_name="Blocked Person", author_handle="blockedperson", published_at="2026-11-07T08:00:00+00:00"),
]
items += [i for i in (linkedin.normalise(r, "profiles") for r in json.loads((fx / "linkedin.json").read_text())) if i]
items += pubmed.parse_efetch((fx / "pubmed.xml").read_text())
items += rss.parse_feed((fx / "rss.xml").read_text(), "Test feed", 10)
print("fixture items:", len(items))
added = collect.add_items(items)
print("added:", added)
assert added == len(items) - 1, added  # opted-out author excluded (LinkedIn repost already dropped by normalise)
assert collect.add_items(items) == 0, "dedupe failed"
stats = process.run()
print("stats:", stats)
assert stats["published"] >= 3, stats
n = build.build()
amplify.run(stats)  # no credentials -> no-ops
site = Path("site")
for p in ["index.html", "rss.xml", "sitemap.xml", "news-sitemap.xml", "voices/index.html", "about/index.html"]:
    assert (site / p).exists(), p
arts = list((site / "news").glob("*/index.html"))
assert len(arts) == n and n >= 3
html = arts[0].read_text()
assert "Original source" in html and "application/ld+json" in html
# LinkedIn page posting (mocked API, nothing leaves the machine)
from lungdaily import linkedin_share
assert linkedin_share.escape("COPD (GOLD) #1 @x") == "COPD \\(GOLD\\) \\#1 \\@x"
assert linkedin_share.hashtag("#COPD") == "{hashtag|\\#|COPD}"
import os as _os, json as _json
from lungdaily.util import POSTS as _POSTS
calls = []
class _R:
    def __init__(self, code=201, js=None, headers=None):
        self.status_code, self._js, self.headers, self.text = code, js or {}, headers or {}, ""
        self.ok = code < 400
    def json(self): return self._js
def _post(url, **kw):
    calls.append(url)
    if "initializeUpload" in url:
        return _R(200, {"value": {"uploadUrl": "https://upload.example/x", "image": "urn:li:image:1"}})
    body = kw["json"]
    assert body["author"] == "urn:li:organization:123" and body["content"]["article"]["source"].startswith("http")
    assert body["content"]["article"].get("thumbnail") == "urn:li:image:1"
    return _R(201, headers={"x-restli-id": "urn:li:share:999"})
linkedin_share.requests.post = _post
linkedin_share.requests.put = lambda url, **kw: _R(201)
_os.environ.update(LINKEDIN_ORG_ID="123", LINKEDIN_ACCESS_TOKEN="t")
first = sorted(_POSTS.glob("*.json"))[0]
rec = _json.loads(first.read_text())
assert linkedin_share.post(rec, "https://lungdaily.org/news/x/", {}, ["COPD"]) == "urn:li:share:999"
# post-deploy runner, direct API route
assert linkedin_share.run_pending() >= 1
assert _json.loads(first.read_text())["shared"].get("linkedin") == "urn:li:share:999"
# LinkedIn-sourced article -> repost of the original
li = dict(rec, source=dict(rec["source"], source="linkedin", extra={"urn": "urn:li:share:42"}))
seen = []
def _post2(url, **kw):
    seen.append(kw.get("json", {}))
    return _R(200, {"value": {"uploadUrl": "https://u", "image": "urn:li:image:1"}}) if "initializeUpload" in url \
        else _R(201, headers={"x-restli-id": "urn:li:share:1000"})
linkedin_share.requests.post = _post2
assert linkedin_share.post(li, "https://lungdaily.org/news/x/", {}, []) == "urn:li:share:1000"
assert seen[-1]["reshareContext"]["parent"] == "urn:li:share:42" and "content" not in seen[-1]
assert "Read on LungDaily: https://lungdaily.org/news/x/" in seen[-1]["commentary"]
# Make.com route: webhook gets text with link + hashtags and the live card image URL
for k in ("LINKEDIN_ORG_ID", "LINKEDIN_ACCESS_TOKEN"):
    _os.environ.pop(k)
_os.environ["MAKE_LINKEDIN_WEBHOOK"] = "https://hook.make.example/abc"
hooks = []
def _post3(url, **kw):
    hooks.append((url, kw["json"]))
    return _R(200)
linkedin_share.requests.post = _post3
for f in _POSTS.glob("*.json"):
    d = _json.loads(f.read_text()); d.pop("shared", None); f.write_text(_json.dumps(d))
n_li = linkedin_share.run_pending()
assert n_li >= 3 and len(hooks) == n_li, (n_li, len(hooks))
u, pl = hooks[0]
assert u == "https://hook.make.example/abc"
assert "Read more: https://" in pl["text"] and pl["image_url"].split("?")[0].endswith(".png")
assert all(_json.loads(f.read_text())["shared"]["linkedin"] == "make" for f in _POSTS.glob("*.json")
           if _json.loads(f.read_text()).get("status") == "published")
assert linkedin_share.run_pending() == 0, "must not post twice"
_os.environ.pop("MAKE_LINKEDIN_WEBHOOK")
for k in ():
    _os.environ.pop(k)

# fair selection: one per author per run, boosts for people/approvals and individuals
def _it(i, h, score, cat="research", at="organisation"):
    return {"id": str(i), "author_handle": h, "source": "x", "metrics": {}, "extra": {},
            "triage": {"score": score, "category": cat, "author_type": at}}
_sel = {"max_per_author_per_run": 1, "max_per_author_per_day": 2, "person_boost": 0.5,
        "category_boost": {"people": 1.0}}
_c = [_it(1, "ERSpublications", 9), _it(2, "ERSpublications", 9), _it(3, "ERSpublications", 8.5),
      _it(4, "drsmith", 8, at="person"), _it(5, "society", 7, cat="people")]
_c.sort(key=lambda i: process.rank(i, _sel), reverse=True)
_ch, _rest = process.pick(_c, 3, _sel)
_ids = [i["id"] for i in _ch]
assert _ids.count("1") + _ids.count("2") + _ids.count("3") == 1, _ids
assert "4" in _ids and "5" in _ids, _ids

# editor-supplied picture replaces the branded thumbnail and share card
from PIL import Image as _Img
from lungdaily import images as _images
_images.IMAGES.mkdir(parents=True, exist_ok=True)
_Img.new("RGB", (900, 900), (200, 120, 60)).save(_images.IMAGES / f"{rec['slug']}.jpg")
build.build()
_card = _Img.open(site / "cards" / f"{rec['slug']}.png")
assert _card.size == (1200, 630)
assert _card.getpixel((600, 120))[0] > 150, "custom picture not used"
_page = (site / "news" / rec["slug"] / "index.html").read_text()
assert 'class="hero"' in _page
(_images.IMAGES / f"{rec['slug']}.jpg").unlink()
_home = (site / "index.html").read_text()

# takedown
slug = arts[0].parent.name
import yaml
cfg = yaml.safe_load(open("config/optout.yaml")); cfg["takedown"] = [slug]
yaml.safe_dump(cfg, open("config/optout.yaml", "w"))
assert build.build() == n - 1
print("OK — articles built:", n, "| example:", slug)
'''


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="lungdaily-test-"))
    dst = tmp / "proj"
    shutil.copytree(PROJECT, dst, ignore=shutil.ignore_patterns("site", "__pycache__", ".git"))
    for d in ("data", "content/posts"):
        shutil.rmtree(dst / d, ignore_errors=True)
        (dst / d).mkdir(parents=True)
    opt = dst / "config" / "optout.yaml"
    opt.write_text("x_handles: [blockedperson]\nlinkedin_urls: []\nnames: []\ntakedown: []\n")
    env = {**os.environ, "LUNGDAILY_FAKE_LLM": "1", "LUNGDAILY_NOW": "2026-11-07T10:00:00+00:00", "PYTHONPATH": str(dst)}
    for k in ("X_BEARER_TOKEN", "APIFY_TOKEN", "TELEGRAM_BOT_TOKEN", "X_API_KEY"):
        env.pop(k, None)
    res = subprocess.run([sys.executable, "-c", SCRIPT], cwd=dst, env=env)
    if res.returncode == 0:
        print("Built test site at:", dst / "site")
    return res.returncode


if __name__ == "__main__":
    sys.exit(main())
