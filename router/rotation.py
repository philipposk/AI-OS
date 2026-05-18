"""Generic free-model rotation for any provider.

A `Rotator` keeps a per-provider cached list of usable models, ranks them by a
hand-tuned preference table, picks one to start from (last successful), and
rotates on rate-limit / transient HTTP errors. Sticky cursor keeps the next
call on the same model so cache locality doesn't reset each call.

Subclass and supply:
  - PROVIDER name (used as a table-name slug)
  - PREF dict of model_id → weight
  - list_models(api_key) -> List[FreeModel]
"""
from __future__ import annotations

import json
import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Set, Tuple

import requests

from .base import ChatResult, Message

logger = logging.getLogger(__name__)

ROTATE_STATUSES: Set[int] = {429, 502, 503, 504}
ROTATE_SENTINELS = {"free-rotate", "free-rotation", "rotate-free"}
CACHE_TTL_SEC = int(os.getenv("ROUTER_FREE_TTL", "3600"))


@dataclass
class FreeModel:
    id: str
    context_length: int
    weight: float


class Rotator(ABC):
    PROVIDER: str = "base"
    PREF: dict[str, float] = {}

    @abstractmethod
    def list_models(self, api_key: str) -> List[FreeModel]:
        """Hit the provider's models endpoint, filter to text-chat-capable, return ranked list."""

    # ---------- shared cache (single SQLite table per provider) ----------

    @classmethod
    def _table(cls) -> str:
        return f"{cls.PROVIDER}_free"

    @classmethod
    def _ensure_schema(cls, conn) -> None:
        conn.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS {cls._table()} (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                models_json TEXT NOT NULL,
                refreshed_at TEXT NOT NULL,
                last_used TEXT
            );
            """
        )

    @classmethod
    def _connect(cls):
        from storage.db import connect

        conn = connect()
        cls._ensure_schema(conn)
        return conn

    @classmethod
    def _save_cache(cls, models: List[FreeModel], last_used: Optional[str] = None) -> None:
        with cls._connect() as conn:
            ts = datetime.now(timezone.utc).isoformat()
            conn.execute(
                f"INSERT INTO {cls._table()} (id, models_json, refreshed_at, last_used) "
                "VALUES (1, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                f"models_json=excluded.models_json, refreshed_at=excluded.refreshed_at, "
                f"last_used=COALESCE(excluded.last_used, {cls._table()}.last_used)",
                (json.dumps([m.__dict__ for m in models]), ts, last_used),
            )

    @classmethod
    def _save_last_used(cls, model_id: str) -> None:
        with cls._connect() as conn:
            conn.execute(f"UPDATE {cls._table()} SET last_used=? WHERE id=1", (model_id,))

    @classmethod
    def _load_cache(cls) -> Optional[Tuple[List[FreeModel], float, Optional[str]]]:
        with cls._connect() as conn:
            row = conn.execute(
                f"SELECT models_json, refreshed_at, last_used FROM {cls._table()} WHERE id=1"
            ).fetchone()
            if row is None:
                return None
            models = [FreeModel(**m) for m in json.loads(row["models_json"])]
            refreshed = datetime.fromisoformat(row["refreshed_at"]).timestamp()
            return models, refreshed, row["last_used"]

    # ---------- public ----------

    def get_models(self, api_key: str, force_refresh: bool = False) -> Tuple[List[FreeModel], Optional[str]]:
        cached = self._load_cache()
        if cached and not force_refresh:
            models, refreshed, last_used = cached
            if time.time() - refreshed < CACHE_TTL_SEC and models:
                return models, last_used
        models = self.list_models(api_key)
        self._save_cache(models)
        cached = self._load_cache()
        return models, (cached[2] if cached else None)

    def _ordered(self, models: List[FreeModel], last_used: Optional[str]) -> List[FreeModel]:
        if not last_used:
            return list(models)
        ids = [m.id for m in models]
        if last_used not in ids:
            return list(models)
        idx = ids.index(last_used)
        return models[idx:] + models[:idx]

    def chat_rotate(
        self,
        client,
        messages: List[Message],
        *,
        max_tokens: int,
        temperature: float,
        skip: Optional[Set[str]] = None,
    ) -> ChatResult:
        if not client.api_key:
            from .base import ProviderUnavailable
            raise ProviderUnavailable(f"{self.PROVIDER}: no API key")
        skip = set(skip or [])
        models, last_used = self.get_models(client.api_key)
        if not models:
            raise RuntimeError(f"{self.PROVIDER}: no free models available")
        ordered = self._ordered(models, last_used)
        errors: list[str] = []
        for m in ordered:
            if m.id in skip:
                continue
            try:
                res = client._chat_one(messages, m.id, max_tokens=max_tokens, temperature=temperature)
                self._save_last_used(m.id)
                logger.info("%s rotate: %s succeeded", self.PROVIDER, m.id)
                return res
            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else 0
                if status in ROTATE_STATUSES:
                    errors.append(f"{m.id}: {status}")
                    logger.info("%s rotate: %s -> %s, next", self.PROVIDER, m.id, status)
                    continue
                raise
            except requests.exceptions.RequestException as e:
                errors.append(f"{m.id}: {type(e).__name__}")
                logger.info("%s rotate: %s -> %s, next", self.PROVIDER, m.id, type(e).__name__)
                continue
        raise RuntimeError(
            f"{self.PROVIDER}: all free models rate-limited / unavailable. Tried: " + " | ".join(errors)
        )


# ---------- helpers reused by subclasses ----------


def is_text_chat(rec: dict) -> bool:
    """OpenRouter-shape architecture filter."""
    arch = rec.get("architecture") or {}
    inp = arch.get("input_modalities") or []
    out = arch.get("output_modalities") or []
    modality = arch.get("modality") or ""
    if inp and ("text" not in inp or "image" in inp or "audio" in inp):
        return False
    if out and "text" not in out:
        return False
    if modality and modality != "text->text":
        return False
    return True


def rank(models: List[FreeModel]) -> List[FreeModel]:
    models.sort(key=lambda m: (-m.weight, m.id))
    return models
