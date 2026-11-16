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
# HarvestAPI shape: own post kept (URL query stripped, focus match), repost + image-only skipped
_hv = {"type": "post", "id": "752", "linkedinUrl": "https://www.linkedin.com/posts/tonya-winders-7109169_activity-752",
       "content": "COPD update text", "author": {"name": "Tonya Winders", "info": "President of GAAPP",
       "linkedinUrl": "https://www.linkedin.com/in/tonya-winders-7109169?miniProfileUrn=x"},
       "postedAt": {"timestamp": 1794505468595}, "engagement": {"likes": 44, "comments": 1, "shares": 4}, "header": {"text": None}}
_n = linkedin.normalise(_hv, "profiles")
assert _n and _n["author_url"] == "https://www.linkedin.com/in/tonya-winders-7109169" and _n["metrics"]["likes"] == 44
from lungdaily.util import is_focus as _isf
assert _isf({"author_url": _n["author_url"]})
assert linkedin.normalise(dict(_hv, header={"text": "Tonya Winders reposted this"}), "profiles") is None
assert linkedin.normalise(dict(_hv, content=None), "profiles") is None
items += pubmed.parse_efetch((fx / "pubmed.xml").read_text())
items += rss.parse_feed((fx / "rss.xml").read_text(), "Test feed", 10)
print("fixture items:", len(items))
added = collect.add_items(items)
print("added:", added)
assert added == len(items) - 1, added  # opted-out author excluded (LinkedIn repost already dropped by normalise)
assert collect.add_items(items) == 0, "dedupe failed"
# quality gate: headline-only / teaser sources are not written up (default thresholds)
assert process.too_thin({"source": "rss", "text": "WHO releases guidance."})
assert not process.too_thin({"source": "x", "text": "New ERS statement on COPD case-finding in primary care: spirometry for adults over 40."})
_fa = {"headline": "H", "dek": "d", "body": "Novocure won approval. The item gives no further detail. " + "word " * 100, "key_quote": ""}
assert any("thin article" in s for s in process.check({"triage": {"category": "approvals"}, "title": "", "text": "x", "source": "rss"}, _fa)["issues"])
# the fixtures are deliberately short: lower the bar for the rest of the end-to-end test
import yaml as _yq
_cq = _yq.safe_load(open("config/settings.yaml")); _cq["publishing"]["min_source_words"] = {"rss": 5, "pubmed": 5, "x": 5, "linkedin": 5}
_yq.safe_dump(_cq, open("config/settings.yaml", "w"))
stats = process.run()
# weekly per-account cap (settings selection.max_per_author_per_week)
_wk = {"max_per_author_per_run": 5, "max_per_author_per_day": 9, "max_per_author_per_week": {"ERSpublications": 0}}
_ers_items = [{"author_handle": "ERSpublications", "id": "w1"}, {"author_handle": "someoneelse", "id": "w2"}]
_ch, _rest = process.pick(_ers_items, 5, _wk)
assert [c["id"] for c in _ch] == ["w2"] and [r["id"] for r in _rest] == ["w1"], (_ch, _rest)
print("stats:", stats)
assert stats.get("ready", 0) >= 3 and stats["published"] == 0, stats
# scheduled publishing: one ready article per due slot (10:00 UTC = 11:00 CET -> 5 slots due)
import os as _o
_o.environ["LUNGDAILY_NOW"] = "2026-11-07T05:30:00+00:00"   # 06:30 CET: no slot due yet
assert process.publish_slot() is None
_o.environ["LUNGDAILY_NOW"] = "2026-11-07T10:00:00+00:00"
_pub = [process.publish_slot() for _ in range(stats["ready"])]
assert all(_pub) and len(set(_pub)) == len(_pub), _pub
assert process.publish_slot() is None          # queue empty
assert read_json(DATA / "slots.json", {})["count"] == len(_pub)
stats["published"] = len(_pub)
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

# LinkedIn organisation tags (little text) — uses linkedin_id from voices.yaml
import yaml as _y
_vp = Path("config/voices.yaml"); _vd = _y.safe_load(_vp.read_text())
for _v in _vd["voices"]:
    _v.pop("linkedin_id", None)      # test with its own fake ids only
_vd["voices"].append({"name": "Global Initiative for Asthma", "aliases": ["GINA"], "x": "ginasthma", "linkedin_id": 111})
_vd["voices"].append({"name": "European Respiratory Society", "aliases": ["ERS"], "linkedin_id": 222})
_vp.write_text(_y.safe_dump(_vd))
_r = {"headline": "GINA (2026) report out", "dek": "New report.", "body": "ERS welcomed it.", "people": [],
      "source": {"author_name": "GINA", "author_handle": "ginasthma"}, "slug": "x"}
_m = linkedin_share.linkedin_mentions(_r)
assert _m[0] == ("Global Initiative for Asthma", "urn:li:organization:111", True), _m
assert ("European Respiratory Society", "urn:li:organization:222", False) in _m, _m
_t = linkedin_share.little_text(_r, "https://lungdaily.org/news/x/", ["COPD"], _m)
assert "Source: @[Global Initiative for Asthma](urn:li:organization:111)" in _t, _t
assert "Featuring: @[European Respiratory Society](urn:li:organization:222)" in _t, _t
assert "GINA \\(2026\\)" in _t and "{hashtag|\\#|COPD}" in _t, _t
_r2 = dict(_r, source={"author_name": "Medical Xpress"})
assert "Source: Medical Xpress" in linkedin_share.little_text(_r2, "u", [], linkedin_share.linkedin_mentions(_r2))
# people: tagged only with linkedin_tag_people on, matched by LinkedIn profile URL, exact profile name
_vd["voices"].append({"name": "Jane Roe", "linkedin": "https://www.linkedin.com/in/jane-roe-1/", "linkedin_name": "Jane Roe, MD",
                      "linkedin_person_urn": "urn:li:person:AbC123"})
_vp.write_text(_y.safe_dump(_vd))
_r3 = dict(_r, headline="Asthma remission", body="x", source={"author_name": "Jane Roe", "author_url": "https://www.linkedin.com/in/jane-roe-1"})
assert linkedin_share.linkedin_mentions(_r3) == []
_sp = Path("config/settings.yaml"); _sd = _y.safe_load(_sp.read_text()); _sd["amplify"]["linkedin_tag_people"] = True
_sp.write_text(_y.safe_dump(_sd))
assert linkedin_share.linkedin_mentions(_r3) == [("Jane Roe, MD", "urn:li:person:AbC123", True)], linkedin_share.linkedin_mentions(_r3)
assert "Source: @[Jane Roe, MD](urn:li:person:AbC123)" in linkedin_share.little_text(_r3, "u", [], linkedin_share.linkedin_mentions(_r3))
_sd["amplify"]["linkedin_tag_people"] = False
_sp.write_text(_y.safe_dump(_sd))

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

# People section: page text extraction, Bing redirect unwrap, Bing <News:Source>, short-brief length
from lungdaily.sources import page as _pg
assert _pg.real_url("http://www.bing.com/news/apiclick.aspx?ref=FexRss&url=https%3a%2f%2fexample.org%2fa&c=1") == "https://example.org/a"
_html = "<html><nav><p>Menu menu menu menu menu menu menu menu menu menu</p></nav><script>x</script>" \
        "<p>Professor Jane Roe has been elected president of the Example Thoracic Society, the society said.</p><p>short</p></html>"
assert _pg.extract_text(_html) == "Professor Jane Roe has been elected president of the Example Thoracic Society, the society said.", _pg.extract_text(_html)
_bing = ('<rss xmlns:News="https://www.bing.com/news/search?q=x&amp;format=rss"><channel><item><title>Roe elected ETS president</title>'
         '<link>http://www.bing.com/news/apiclick.aspx?url=https%3a%2f%2fexample.org%2fb</link><description>Jane Roe was elected.</description>'
         '<pubDate>Wed, 11 Nov 2026 10:00:00 GMT</pubDate><News:Source>Example News</News:Source></item></channel></rss>')
_bi = rss.parse_feed(_bing, "Bing test", 5)[0]
assert _bi["author_name"] == "Example News", _bi
_p = {"triage": {"category": "people"}, "title": "", "text": "x", "source": "rss"}
_a = {"headline": "Jane Roe: elected president", "body": "word " * 60, "key_quote": ""}
assert not any("length" in s for s in process.check(_p, _a)["issues"])
_p["triage"]["category"] = "research"
assert any("length" in s for s in process.check(_p, _a)["issues"])
from lungdaily.sources.x import build_queries as _bq
assert any(k == "people" for k, _ in _bq())

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

# awareness days: date rules, windows, hashtags, ranking boost, banner + day page
import datetime as _dt
from lungdaily import awareness as _aw
assert _aw.date_for("third wednesday of november", 2026) == _dt.date(2026, 11, 18)
assert _aw.date_for("third wednesday of november", 2027) == _dt.date(2027, 11, 17)
assert _aw.date_for("first tuesday of may", 2026) == _dt.date(2026, 5, 5)
assert _aw.date_for("last friday of december", 2026) == _dt.date(2026, 12, 25)
assert _aw.date_for("03-24", 2027) == _dt.date(2027, 3, 24)
_at = _dt.datetime(2026, 11, 16, 9, tzinfo=_dt.timezone.utc)
assert [d["slug"] for d in _aw.active(_at)] == ["world-copd-day"]
assert not any(d["slug"] == "world-copd-day" for d in _aw.active(_dt.datetime(2026, 11, 20, tzinfo=_dt.timezone.utc)))
assert _aw.matches({"match": [" TB "]}, "new TB drug") and not _aw.matches({"match": [" TB "]}, "TBS and HTTP")
_o.environ["LUNGDAILY_NOW"] = "2026-11-16T09:00:00+00:00"
assert any(k == "awareness" and "#WorldCOPDDay" in q for k, q in _bq())
assert any("World COPD Day" in f["name"] for f in _aw.rss_feeds())
_copd = {"headline": "GOLD: early COPD diagnosis", "body": "Spirometry matters.", "tags": ["copd"]}
assert amplify.x_text(_copd, "https://lungdaily.org/x", {"x_hashtags": 2}).count("#WorldCOPDDay") == 1
_ai = {"id": "9", "title": "COPD", "text": "COPD case-finding", "triage": {"score": 8, "category": "research"}}
assert process.rank(_ai, {"awareness_boost": 1.0}) == 9.0 and process.rank(_ai, {}) == 8.0
build.build()
_home = (site / "index.html").read_text()
assert 'class="awareness"' in _home and "World COPD Day is on Wednesday 18 November" in _home, "banner missing"
_day = (site / "days" / "world-copd-day" / "index.html").read_text()
assert "World COPD Day 2026" in _day and "news/" in _day, "day page missing coverage"
assert "/days/world-copd-day/" in (site / "sitemap.xml").read_text()
_o.environ["LUNGDAILY_NOW"] = "2026-11-07T10:00:00+00:00"
build.build()
_copd = (site / "days" / "world-copd-day" / "index.html").read_text()
assert "Early Diagnosis and Management for a Better Life" in _copd and "3.5 million" in _copd and "in 11 days" in _copd
assert "/days/world-asthma-day/" in (site / "days" / "index.html").read_text()   # whole calendar, not just active days
assert (site / "reports" / "index.html").exists() and (site / "newsletter" / "index.html").exists()
# congress daily: composes on a congress day (no API key -> composed only)
_o.environ["LUNGDAILY_NOW"] = "2027-05-16T18:00:00+00:00"
from lungdaily import newsletter as _nlc
assert _nlc.congress_daily() is False
_o.environ["LUNGDAILY_NOW"] = "2026-11-07T10:00:00+00:00"
assert 'class="awareness"' not in (site / "index.html").read_text()
_sm = (site / "sitemap.xml").read_text()
assert "<lastmod>" in _sm and "/about/" in _sm
_art = arts[0].read_text()
assert '"author"' in _art and '"dateModified"' in _art

# "Send us your news" page + footer link (newsletter form off while no Buttondown username)
import yaml as _y0
_c0 = _y0.safe_load(open("config/settings.yaml")); _c0["newsletter"]["buttondown_username"] = ""
_y0.safe_dump(_c0, open("config/settings.yaml", "w"))
build.build()
assert (site / "submit-news" / "index.html").exists() and "/submit-news/" in (site / "sitemap.xml").read_text()
assert "Send us news" in (site / "index.html").read_text()
assert 'class="newsletter"' not in (site / "index.html").read_text()     # no Buttondown username yet

# featured emails: source matching, compose, preview mode + weekly cap
from lungdaily import featured as _fe
_ers = next(r for r in (json.loads(x.read_text()) for x in POSTS.glob("*.json"))
            if r.get("status") == "published" and (r.get("source") or {}).get("author_handle") == "ERSpublications")
assert _fe.source_voice(_ers)["press_email"] == "press@ersnet.org"
_subj, _txt, _html = _fe.compose(_ers, _fe.source_voice(_ers), "https://lungdaily.org", {"name": "LungDaily", "tagline": "The Pulse of Respiratory Health", "x_handle": "LungDaily"})
assert _ers["headline"][:40] in _subj and "/cards/" in _txt and "stop" in _txt and "<img" in _html
# sharing kit: ready-made LinkedIn post, featured card, tracked links, badge, newsletter sign-up
assert "READY TO POST ON LINKEDIN" in _txt and "/cards/featured/" in _txt and "utm_campaign=featured" in _txt
assert "/featured/" in _txt and "/newsletter/" in _txt and "Share on LinkedIn" in _html
assert (site / "cards" / "featured" / f"{_ers['slug']}.png").exists()
assert (site / "badges" / "featured-in-lungdaily.png").exists() and (site / "badges" / "featured-in-lungdaily.svg").exists()
assert "badge code" in (site / "featured" / "index.html").read_text().lower()
# reshare tracking
from lungdaily import reshares as _rs
assert _rs.slugs_in("see https://lungdaily.org/news/2026-11-07-abc/?utm_source=x") == ["2026-11-07-abc"]
_rs.note_featured(_ers["slug"], "European Respiratory Society", "press@ersnet.org")
assert _rs.record(_ers["slug"], "ERS", "ERSpublications", "x", "https://x.com/ERSpublications/status/9")
assert not _rs.record(_ers["slug"], "ERS", "ERSpublications", "x", "https://x.com/ERSpublications/status/9")   # dedupe
assert _rs.from_linkedin_rows([{"content": "Proud to be featured: https://lungdaily.org/news/" + _ers["slug"] + "/",
                                "linkedinUrl": "https://www.linkedin.com/posts/ers_1", "author": {"name": "ERS"}}]) == 1
_rr = _rs.report()
assert _rr["featured"] == 1 and _rr["reshared"] == 1 and _rr["rate"] == 100 and _rr["by_channel"] == {"x": 1, "linkedin": 1}, _rr
_o.environ["LUNGDAILY_NOW"] = "2026-11-07T12:00:00+00:00"
_prev = _fe.run()
assert [e["to"] for e in _prev] == ["press@ersnet.org"] and not _prev[0]["sent"], _prev
assert _fe.run() == []                       # already previewed / weekly cap
# Bluesky text + hashtag facets (byte offsets)
from lungdaily import bluesky as _bs
_t, _f = _bs.post_text({"headline": "Lung health ü study"}, ["#COPD", "#WorldCOPDDay"])
assert _t == "Lung health ü study\n\n#COPD #WorldCOPDDay"
_b = _t.encode()
assert [_b[f["index"]["byteStart"]:f["index"]["byteEnd"]].decode() for f in _f] == ["#COPD", "#WorldCOPDDay"]
assert len(_bs.post_text({"headline": "x" * 400}, ["#COPD"])[0]) <= 300
assert _bs.run_pending() == 0                # no credentials -> no-op
# newsletter: pick + compose (no API key -> composed only)
from lungdaily import newsletter as _nl
_picked = _nl.pick(days=30)
assert len(_picked) >= 3
_s2, _body = _nl.compose(_picked, {"name": "LungDaily", "tagline": "The Pulse of Respiratory Health", "x_handle": "LungDaily"}, "https://lungdaily.org")
assert _s2.startswith("The Week in Respiratory Health") and _body.count("### [") == len(_picked)
assert "/newsletter/" in _body                 # forward-to-a-colleague line
_ts, _tb = _nl.translate(_s2, _body, "es")     # fake LLM: returns the text; real run translates
assert _ts.startswith("[ES]") and "Traducido del inglés" in _tb
assert _nl._filters([("contains", "lang-es")])["filters"][0] == {"operator": "contains", "field": "subscriber.tags", "value": "lang-es"}
# monthly report (needs >= 10 articles in a finished month)
from lungdaily.reports import build_reports as _br
import datetime as _dt
_fake = [dict(json.loads(next(POSTS.glob("*.json")).read_text()), slug=f"s{i}", tags=["copd"], key_quote="q",
              category="research", score=8, date=_dt.datetime(2026, 10, 3, tzinfo=_dt.timezone.utc)) for i in range(12)]
_rep = _br(_fake)
assert _rep and _rep[0]["key"] == "2026-10" and _rep[0]["sections"][0]["label"] == "COPD" and len(_rep[0]["studies"]) == 10
# Q&A: refused without consent, published as given with consent
from lungdaily import submit as _sub
Path("content/qa").mkdir(parents=True, exist_ok=True)
_qa = Path("content/qa/test.md")
_qa.write_text("---\nname: Dr Jane Roe\nrole: Professor\nheadline: 'Q&A: Jane Roe on spirometry'\nlink: https://example.org/roe\ntags: [copd]\nconsent: no\n---\nIntro.\n\n## Why spirometry?\n\nBecause it works.\n")
assert _sub.qa("content/qa/test.md") is None
_qa.write_text(_qa.read_text().replace("consent: no", "consent: yes"))
_qs = _sub.qa("content/qa/test.md")
_qr = json.loads((POSTS / f"{_qs}.json").read_text())
assert _qr["status"] == "ready" and "**Why spirometry?**" in _qr["body"] and "Because it works." in _qr["body"]
# newsletter form + congress banner
import yaml as _y
_cfg = _y.safe_load(open("config/settings.yaml")); _cfg["newsletter"]["buttondown_username"] = "lungdaily"
_y.safe_dump(_cfg, open("config/settings.yaml", "w"))
_o.environ["LUNGDAILY_NOW"] = "2027-05-16T09:00:00+00:00"
build.build()
_home = (site / "index.html").read_text()
assert "embed-subscribe/lungdaily" in _home
assert "ATS 2027 is on now, until 19 May" in _home and "New Orleans" in _home, "congress banner"
assert (site / "days" / "ats-2027" / "index.html").exists()
_ats = (site / "days" / "ats-2027" / "index.html").read_text()
assert "Share kit" in _ats and "#ATS2027" in _ats and (site / "cards" / "days" / "ats-2027.png").exists()
assert "nl-a-email" in next((site / "news").glob("*/index.html")).read_text()   # sign-up box inside every article
assert 'value="lang-es"' in _home
_o.environ["LUNGDAILY_NOW"] = "2026-11-07T10:00:00+00:00"
build.build()

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
