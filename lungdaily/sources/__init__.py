"""Source collectors. Each returns a list of normalised items:

{id, source, url, title, text, author_name, author_handle, author_url, author_headline,
 published_at, metrics{likes, reposts, comments}, extra{}}
"""
from __future__ import annotations


def make_item(source: str, native_id: str, **kw) -> dict:
    item = {
        "id": f"{source}:{native_id}",
        "source": source,
        "url": "",
        "title": "",
        "text": "",
        "author_name": "",
        "author_handle": "",
        "author_url": "",
        "author_headline": "",
        "published_at": "",
        "metrics": {"likes": 0, "reposts": 0, "comments": 0},
        "extra": {},
    }
    item.update({k: v for k, v in kw.items() if v is not None})
    return item
