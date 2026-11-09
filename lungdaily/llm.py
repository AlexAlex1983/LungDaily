"""Minimal Claude Messages API client returning parsed JSON.

Set ANTHROPIC_API_KEY. For offline tests set LUNGDAILY_FAKE_LLM=1 (deterministic stub output).
"""
from __future__ import annotations

import json
import os
import re
import time

import requests

from .util import log, settings

API_URL = "https://api.anthropic.com/v1/messages"


class LLMError(RuntimeError):
    pass


def _extract_json(text: str):
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    elif text.startswith("```"):          # opening fence without a closing one
        text = re.sub(r"^```(?:json)?\s*", "", text)
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise LLMError(f"No JSON in model output: {text[:200]}")


def ask_json(role: str, system: str, user: str, fake=None):
    """role: triage | writer | checker (selects model from settings)."""
    if os.environ.get("LUNGDAILY_FAKE_LLM") == "1":
        if fake is None:
            raise LLMError("fake LLM requested but no fake output given")
        return fake() if callable(fake) else fake

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise LLMError("ANTHROPIC_API_KEY is not set")
    cfg = settings()["models"]
    body = {
        "model": cfg[role],
        "max_tokens": (cfg.get("triage_max_tokens", 8000) if role == "triage"
                       else cfg.get("max_tokens", 1500)),
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    last = None
    for attempt in range(4):
        try:
            r = requests.post(API_URL, headers=headers, json=body, timeout=120)
            if r.status_code in (429, 500, 502, 503, 529):
                raise LLMError(f"HTTP {r.status_code}: {r.text[:200]}")
            r.raise_for_status()
            data = r.json()
            text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
            if data.get("stop_reason") == "max_tokens":
                log.warning("LLM %s reply hit max_tokens (%s) — raise models.%s in settings.yaml",
                            role, body["max_tokens"], "triage_max_tokens" if role == "triage" else "max_tokens")
            return _extract_json(text)
        except (requests.RequestException, LLMError, ValueError) as e:
            last = e
            wait = 5 * (attempt + 1)
            log.warning("LLM %s attempt %d failed: %s — retrying in %ss", role, attempt + 1, e, wait)
            time.sleep(wait)
    raise LLMError(f"LLM {role} failed after retries: {last}")
