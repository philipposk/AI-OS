"""NVIDIA NIM free integrate-endpoint rotation.

The integrate endpoint exposes ~100+ models, all free for development. We
filter to instruct-tuned text-chat models, deduplicate, and rotate when one
hits a rate limit.
"""
from __future__ import annotations

import logging
import re
from typing import List

import requests

from .rotation import FreeModel, Rotator, rank

logger = logging.getLogger(__name__)

API_MODELS = "https://integrate.api.nvidia.com/v1/models"

_PREF: dict[str, float] = {
    "meta/llama-3.3-70b-instruct": 10.0,
    "nvidia/llama-3.1-nemotron-70b-instruct": 9.5,
    "deepseek-ai/deepseek-v4-pro": 9.0,
    "deepseek-ai/deepseek-v4-flash": 8.5,
    "meta/llama-3.1-70b-instruct": 8.0,
    "qwen/qwen2.5-coder-32b-instruct": 7.5,
    "google/gemma-4-31b-it": 7.0,
    "mistralai/mistral-large-2-instruct": 6.5,
    "meta/llama-3.1-8b-instruct": 5.0,
    "google/gemma-3-12b-it": 4.5,
    "meta/llama-3.2-3b-instruct": 3.0,
}

# Drop models that aren't text-only chat.
_EXCLUDE_RE = re.compile(
    r"(?:embed|bge-|rerank|guard|deplot|fuyu|vision|whisper|tts|recurrent|"
    r"codegemma|starcoder|codellama|granite-.*-code)",  # code-only specialists go to a separate task type if ever wanted
    re.IGNORECASE,
)


class NvidiaRotator(Rotator):
    PROVIDER = "nvidia"
    PREF = _PREF

    def list_models(self, api_key: str) -> List[FreeModel]:
        r = requests.get(API_MODELS, headers={"Authorization": f"Bearer {api_key}"}, timeout=15)
        r.raise_for_status()
        seen: set[str] = set()
        out: list[FreeModel] = []
        for rec in r.json().get("data", []):
            mid = rec.get("id") or ""
            if not mid or mid in seen or _EXCLUDE_RE.search(mid):
                continue
            seen.add(mid)
            ctx = int(rec.get("context_length") or rec.get("max_input_tokens") or 0)
            out.append(FreeModel(id=mid, context_length=ctx, weight=_PREF.get(mid, 0.0)))
        return rank(out)
