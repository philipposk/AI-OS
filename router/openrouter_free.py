"""OpenRouter free-model rotation."""
from __future__ import annotations

import logging
import os
from typing import List, Optional

import requests

from .rotation import FreeModel, Rotator, ROTATE_SENTINELS, is_text_chat, rank

logger = logging.getLogger(__name__)

API_MODELS = "https://openrouter.ai/api/v1/models"

_PREF: dict[str, float] = {
    "openai/gpt-oss-120b:free": 10.0,
    "nvidia/nemotron-3-super-120b-a12b:free": 9.5,
    "nousresearch/hermes-3-llama-3.1-405b:free": 9.0,
    "z-ai/glm-4.5-air:free": 9.0,
    "qwen/qwen3-next-80b-a3b-instruct:free": 8.5,
    "meta-llama/llama-3.3-70b-instruct:free": 8.0,
    "minimax/minimax-m2.5:free": 7.5,
    "qwen/qwen3-coder:free": 7.0,
    "deepseek/deepseek-v4-flash:free": 6.5,
    "google/gemma-4-31b-it:free": 6.0,
    "google/gemma-4-26b-a4b-it:free": 5.5,
    "openai/gpt-oss-20b:free": 5.0,
    "cognitivecomputations/dolphin-mistral-24b-venice-edition:free": 4.5,
    "arcee-ai/trinity-large-thinking:free": 4.0,
    "nvidia/nemotron-3-nano-30b-a3b:free": 3.5,
    "nvidia/nemotron-nano-12b-v2-vl:free": 3.0,
    "nvidia/nemotron-nano-9b-v2:free": 2.5,
    "meta-llama/llama-3.2-3b-instruct:free": 2.0,
}


def _is_free(rec: dict) -> bool:
    p = rec.get("pricing") or {}
    try:
        return float(p.get("prompt", 1)) == 0 and float(p.get("completion", 1)) == 0
    except (TypeError, ValueError):
        return False


class OpenRouterRotator(Rotator):
    PROVIDER = "openrouter"
    PREF = _PREF

    def list_models(self, api_key: str) -> List[FreeModel]:
        r = requests.get(API_MODELS, headers={"Authorization": f"Bearer {api_key}"}, timeout=15)
        r.raise_for_status()
        out: list[FreeModel] = []
        for rec in r.json().get("data", []):
            if not _is_free(rec) or not is_text_chat(rec):
                continue
            mid = rec["id"]
            out.append(FreeModel(id=mid, context_length=int(rec.get("context_length") or 0), weight=_PREF.get(mid, 0.0)))
        return rank(out)


# ---------- back-compat module-level helpers (used by older tests) ----------

_singleton = OpenRouterRotator()


def fetch_free_models(api_key: str, timeout: int = 15) -> List[FreeModel]:
    return _singleton.list_models(api_key)


def get_models(api_key: str, force_refresh: bool = False):
    return _singleton.get_models(api_key, force_refresh=force_refresh)


def chat_rotate(client, messages, *, max_tokens: int, temperature: float, skip=None):
    return _singleton.chat_rotate(client, messages, max_tokens=max_tokens, temperature=temperature, skip=skip)


def _save_last_used(model_id: str) -> None:
    _singleton._save_last_used(model_id)


def _load_cache():
    return _singleton._load_cache()
