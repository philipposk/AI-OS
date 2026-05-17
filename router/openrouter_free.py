"""OpenRouter free-model rotation.

OpenRouter applies per-model + account-wide rate limits on its `:free` tier.
A single workflow can blow through one model's quota mid-run. This module
caches the live list of text-chat-capable free models, ranks them by a small
hand-tuned preference table, and rotates on 429/502/503.

Usage:
- Call OpenRouterClient with model id `free-rotate` (or env
  `ROUTER_OPENROUTER_DEFAULT=free-rotate`). The client delegates to
  `chat_rotate()` below.
- CLI: `python -m router.openrouter_free list|refresh|status`

State lives in the shared SQLite db: table `openrouter_free` holds the
cached model list + a single-row cursor of the last successfully used id.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

API_MODELS = "https://openrouter.ai/api/v1/models"
CACHE_TTL_SEC = int(os.getenv("ROUTER_OPENROUTER_FREE_TTL", "3600"))
ROTATE_SENTINELS = {"free-rotate", "free-rotation", "rotate-free"}

# Hand-tuned preference: bigger, instruct-tuned models first. Unknown free
# models inherit weight 0.0 and sort to the end alphabetically.
_PREF: dict[str, float] = {
    "openai/gpt-oss-120b:free": 10.0,
    "nvidia/nemotron-3-super-120b-a12b:free": 9.5,
    "z-ai/glm-4.5-air:free": 9.0,
    "nousresearch/hermes-3-llama-3.1-405b:free": 9.0,
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

# Rate limit / transient HTTP statuses that trigger model rotation.
ROTATE_STATUSES = {429, 502, 503, 504}


_SCHEMA = """
CREATE TABLE IF NOT EXISTS openrouter_free (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    models_json TEXT NOT NULL,
    refreshed_at TEXT NOT NULL,
    last_used TEXT
);
"""


@dataclass
class FreeModel:
    id: str
    context_length: int
    weight: float


def _is_text_chat(rec: dict) -> bool:
    arch = rec.get("architecture") or {}
    inp = arch.get("input_modalities") or []
    out = arch.get("output_modalities") or []
    modality = arch.get("modality") or ""
    if "text" not in inp or "text" not in out:
        return False
    if "image" in inp or "audio" in inp:
        # Vision/audio models often fail on plain text-only payloads; skip.
        return False
    if modality and modality != "text->text":
        return False
    return True


def _is_free(rec: dict) -> bool:
    p = rec.get("pricing") or {}
    try:
        return float(p.get("prompt", 1)) == 0 and float(p.get("completion", 1)) == 0
    except (TypeError, ValueError):
        return False


def _connect():
    from storage.db import connect

    conn = connect()
    conn.executescript(_SCHEMA)
    return conn


def fetch_free_models(api_key: str, timeout: int = 15) -> List[FreeModel]:
    """Hit /api/v1/models and return the filtered+ranked free list."""
    r = requests.get(API_MODELS, headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout)
    r.raise_for_status()
    out: list[FreeModel] = []
    for rec in r.json().get("data", []):
        if not _is_free(rec) or not _is_text_chat(rec):
            continue
        mid = rec["id"]
        out.append(
            FreeModel(
                id=mid,
                context_length=int(rec.get("context_length") or 0),
                weight=_PREF.get(mid, 0.0),
            )
        )
    out.sort(key=lambda m: (-m.weight, m.id))
    return out


def _load_cache() -> Optional[Tuple[List[FreeModel], float, Optional[str]]]:
    with _connect() as conn:
        row = conn.execute("SELECT models_json, refreshed_at, last_used FROM openrouter_free WHERE id=1").fetchone()
        if row is None:
            return None
        models = [FreeModel(**m) for m in json.loads(row["models_json"])]
        refreshed = datetime.fromisoformat(row["refreshed_at"]).timestamp()
        return models, refreshed, row["last_used"]


def _save_cache(models: List[FreeModel], last_used: Optional[str] = None) -> None:
    with _connect() as conn:
        ts = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO openrouter_free (id, models_json, refreshed_at, last_used) "
            "VALUES (1, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
            "models_json=excluded.models_json, refreshed_at=excluded.refreshed_at, last_used=COALESCE(excluded.last_used, openrouter_free.last_used)",
            (json.dumps([m.__dict__ for m in models]), ts, last_used),
        )


def _save_last_used(model_id: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE openrouter_free SET last_used=? WHERE id=1", (model_id,))


def get_models(api_key: str, force_refresh: bool = False) -> Tuple[List[FreeModel], Optional[str]]:
    cached = _load_cache()
    if cached and not force_refresh:
        models, refreshed, last_used = cached
        if time.time() - refreshed < CACHE_TTL_SEC and models:
            return models, last_used
    models = fetch_free_models(api_key)
    _save_cache(models)
    cached = _load_cache()
    return models, (cached[2] if cached else None)


def _ordered_for_rotation(models: List[FreeModel], last_used: Optional[str]) -> List[FreeModel]:
    """Start from the one after the last-successful, then rest by preference."""
    if not last_used:
        return list(models)
    ids = [m.id for m in models]
    if last_used not in ids:
        return list(models)
    idx = ids.index(last_used)
    return models[idx:] + models[:idx]


def chat_rotate(
    client,
    messages,
    *,
    max_tokens: int,
    temperature: float,
    skip: Optional[set[str]] = None,
):
    """Try free models in preference order until one responds. Raises if all fail."""
    if not client.api_key:
        from .base import ProviderUnavailable
        raise ProviderUnavailable("OPENROUTER_API_KEY not set")
    skip = set(skip or [])
    models, last_used = get_models(client.api_key)
    if not models:
        raise RuntimeError("No free OpenRouter text models available.")
    ordered = _ordered_for_rotation(models, last_used)
    errors: list[str] = []
    for m in ordered:
        if m.id in skip:
            continue
        try:
            res = client._chat_one(messages, m.id, max_tokens=max_tokens, temperature=temperature)
            _save_last_used(m.id)
            logger.info("openrouter free-rotate: %s succeeded", m.id)
            return res
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else 0
            if status in ROTATE_STATUSES:
                errors.append(f"{m.id}: {status}")
                logger.info("openrouter free-rotate: %s -> %s, trying next", m.id, status)
                continue
            errors.append(f"{m.id}: {status} {str(e)[:120]}")
            # Non-rotatable error (auth, bad request) — stop, surface it.
            raise
        except requests.exceptions.RequestException as e:
            errors.append(f"{m.id}: {type(e).__name__}")
            logger.info("openrouter free-rotate: %s -> %s, trying next", m.id, type(e).__name__)
            continue
    raise RuntimeError(
        "All free OpenRouter models rate-limited or unavailable. Tried: " + " | ".join(errors)
    )


# ---------- CLI ----------


def _cli_list(refresh: bool) -> int:
    from dotenv import dotenv_values

    key = (dotenv_values(".env") or {}).get("OPENROUTER_API_KEY") or os.getenv("OPENROUTER_API_KEY")
    if not key:
        print("OPENROUTER_API_KEY not set")
        return 2
    models, last_used = get_models(key, force_refresh=refresh)
    print(f"# {len(models)} text-chat free models  (last used: {last_used or '—'})")
    for m in models:
        print(f"  {m.weight:5.1f}  ctx={m.context_length:>7d}  {m.id}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="router.openrouter_free")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="Show cached free model list")
    sub.add_parser("refresh", help="Force-refresh from OpenRouter /models")
    args = p.parse_args(argv)
    return _cli_list(refresh=args.cmd == "refresh")


if __name__ == "__main__":
    raise SystemExit(main())
