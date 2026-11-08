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
from lungdaily.util import DATA as _DATA
(_DATA / "amplify_queue.json").write_text(_json.dumps([rec["slug"]]))
amplify.run()
assert _json.loads(first.read_text())["shared"].get("linkedin") == "urn:li:share:999"
for k in ("LINKEDIN_ORG_ID", "LINKEDIN_ACCESS_TOKEN"):
    _os.environ.pop(k)

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
