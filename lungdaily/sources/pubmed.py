"""PubMed via NCBI E-utilities (free; NCBI_API_KEY optional, raises rate limit)."""
from __future__ import annotations

import os
import xml.etree.ElementTree as ET

import requests

from ..util import iso, load_yaml, log, parse_date, settings
from . import make_item

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def collect() -> list[dict]:
    lim = settings()["limits"]
    query = load_yaml("topics.yaml")["pubmed_query"]
    base = {"db": "pubmed", "tool": "lungdaily"}
    if os.environ.get("NCBI_API_KEY"):
        base["api_key"] = os.environ["NCBI_API_KEY"]
    try:
        r = requests.get(f"{EUTILS}/esearch.fcgi", params={
            **base, "term": query, "reldate": 3, "datetype": "edat",
            "retmax": lim["pubmed_max"], "retmode": "json", "sort": "pub_date",
        }, timeout=30)
        r.raise_for_status()
        ids = r.json()["esearchresult"]["idlist"]
        if not ids:
            return []
        r = requests.get(f"{EUTILS}/efetch.fcgi", params={**base, "id": ",".join(ids), "retmode": "xml"}, timeout=60)
        r.raise_for_status()
    except (requests.RequestException, KeyError, ValueError) as e:
        log.warning("PubMed failed: %s", e)
        return []
    return parse_efetch(r.text)


def parse_efetch(xml_text: str) -> list[dict]:
    items = []
    root = ET.fromstring(xml_text)
    for art in root.iter("PubmedArticle"):
        pmid = art.findtext(".//PMID")
        a = art.find(".//Article")
        if a is None or not pmid:
            continue
        title = "".join(a.find("ArticleTitle").itertext()) if a.find("ArticleTitle") is not None else ""
        abstract = " ".join(
            ((p.get("Label") + ": ") if p.get("Label") else "") + "".join(p.itertext())
            for p in a.findall(".//AbstractText")
        )
        if not abstract:
            continue
        journal = a.findtext("Journal/Title") or a.findtext("Journal/ISOAbbreviation") or ""
        authors = []
        for au in a.findall(".//Author"):
            last, fore = au.findtext("LastName"), au.findtext("ForeName")
            if last:
                authors.append(f"{fore} {last}" if fore else last)
        y = art.findtext(".//PubDate/Year") or art.findtext(".//ArticleDate/Year")
        m = art.findtext(".//PubDate/Month") or art.findtext(".//ArticleDate/Month") or "Jan"
        d = art.findtext(".//PubDate/Day") or art.findtext(".//ArticleDate/Day") or "1"
        date = parse_date(f"{y} {m} {d}") if y else None
        if date is None and y:
            date = parse_date(f"{y}-{int(m):02d}-{int(d):02d}") if m.isdigit() else parse_date(f"{y} {m}")
        doi = next((i.text for i in art.findall(".//ArticleId") if i.get("IdType") == "doi"), "")
        pubtypes = [p.text for p in a.findall(".//PublicationType") if p.text]
        items.append(make_item(
            "pubmed", pmid,
            url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            title=title,
            text=abstract,
            author_name=", ".join(authors[:3]) + (" et al." if len(authors) > 3 else ""),
            published_at=iso(date),
            extra={"journal": journal, "doi": doi, "pub_types": pubtypes, "first_author": authors[0] if authors else ""},
        ))
    log.info("PubMed: %d papers", len(items))
    return items
