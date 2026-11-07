"""Shared helpers: paths, config, JSON state, dates, slugs."""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import unicodedata
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
DATA = ROOT / "data"
POSTS = ROOT / "content" / "posts"

logging.basicConfig(level=os.environ.get("LUNGDAILY_LOG", "INFO"), format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("lungdaily")


def load_yaml(name: str) -> dict:
    with open(CONFIG / name, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def settings() -> dict:
    return load_yaml("settings.yaml")


def read_json(path: Path, default):
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=str)
    tmp.replace(path)


def now() -> dt.datetime:
    fixed = os.environ.get("LUNGDAILY_NOW")  # for tests
    if fixed:
        return parse_date(fixed)
    return dt.datetime.now(dt.timezone.utc)


def parse_date(value) -> dt.datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        ts = value / 1000 if value > 1e11 else value
        return dt.datetime.fromtimestamp(ts, dt.timezone.utc)
    s = str(value).strip()
    if s.isdigit():
        return parse_date(int(s))
    s = s.replace("Z", "+00:00")
    for fn in (dt.datetime.fromisoformat,):
        try:
            d = fn(s)
            return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
        except ValueError:
            pass
    from email.utils import parsedate_to_datetime
    try:
        d = parsedate_to_datetime(s)
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError):
        pass
    for fmt in ("%Y %b %d", "%Y %b", "%Y/%m/%d", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s, fmt).replace(tzinfo=dt.timezone.utc)
        except ValueError:
            pass
    return None


def iso(d: dt.datetime | None) -> str:
    return d.astimezone(dt.timezone.utc).isoformat(timespec="seconds") if d else ""


def slugify(text: str, maxlen: int = 70) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:maxlen].rstrip("-") or "item"


def norm_text(s: str) -> str:
    """Normalise text for verbatim-quote checks."""
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s).strip()


def word_count(s: str) -> int:
    return len(re.findall(r"\w+", s or ""))
