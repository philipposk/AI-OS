"""Groq free-tier rotation.

Groq's dev tier exposes every chat model for free, with per-model rate limits.
We filter out non-chat modalities (whisper, orpheus TTS, guards) and rotate
across the remaining text-chat models.
"""
from __future__ import annotations

import logging
import re
from typing import List

import requests

from .rotation import FreeModel, Rotator, rank

logger = logging.getLogger(__name__)

API_MODELS = "https://api.groq.com/openai/v1/models"

_PREF: dict[str, float] = {
    "openai/gpt-oss-120b": 10.0,
    "meta-llama/llama-4-scout-17b-16e-instruct": 9.5,
    "llama-3.3-70b-versatile": 9.0,
    "qwen/qwen3-32b": 8.0,
    "openai/gpt-oss-20b": 7.0,
    "llama-3.1-8b-instant": 6.0,
}

# Anything matching this is excluded regardless of priority.
_EXCLUDE_RE = re.compile(
    r"(?:^whisper|orpheus|prompt-guard|safeguard|allam|compound)",
    re.IGNORECASE,
)


class GroqRotator(Rotator):
    PROVIDER = "groq"
    PREF = _PREF

    def list_models(self, api_key: str) -> List[FreeModel]:
        r = requests.get(API_MODELS, headers={"Authorization": f"Bearer {api_key}"}, timeout=15)
        r.raise_for_status()
        out: list[FreeModel] = []
        for rec in r.json().get("data", []):
            mid = rec.get("id") or ""
            if not mid or _EXCLUDE_RE.search(mid):
                continue
            # Some Groq records include `context_window`, others omit.
            ctx = int(rec.get("context_window") or rec.get("context_length") or 0)
            out.append(FreeModel(id=mid, context_length=ctx, weight=_PREF.get(mid, 0.0)))
        return rank(out)
