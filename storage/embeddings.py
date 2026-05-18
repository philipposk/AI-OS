"""Pluggable text-embedding backends.

Three providers, picked in this order at process start:

  1. Ollama at $OLLAMA_BASE_URL with $EMBEDDINGS_OLLAMA_MODEL
     (default `nomic-embed-text`). Only requires Ollama running locally;
     ~270 MB model, fast on CPU.
  2. sentence-transformers (if installed) with
     $EMBEDDINGS_ST_MODEL (default `all-MiniLM-L6-v2`, ~80 MB).
  3. None → caller falls back to FTS5 keyword search.

The Embedder is constructed lazily and cached. Force a specific backend
with $EMBEDDINGS_BACKEND=ollama|sentence-transformers|none.
"""
from __future__ import annotations

import logging
import math
import os
from abc import ABC, abstractmethod
from typing import List, Optional, Sequence

import requests

logger = logging.getLogger(__name__)


class Embedder(ABC):
    name: str = "base"
    dim: int = 0

    @abstractmethod
    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        ...


class OllamaEmbedder(Embedder):
    name = "ollama"

    def __init__(self, base_url: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.dim = 0  # discovered after first call

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        out: List[List[float]] = []
        for t in texts:
            r = requests.post(
                f"{self.base_url}/api/embeddings",
                json={"model": self.model, "prompt": t},
                timeout=60,
            )
            r.raise_for_status()
            vec = r.json().get("embedding") or []
            if not vec:
                raise RuntimeError(f"Ollama returned empty embedding for model={self.model}")
            if self.dim == 0:
                self.dim = len(vec)
            out.append(vec)
        return out


class SentenceTransformersEmbedder(Embedder):
    name = "sentence-transformers"

    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer  # type: ignore

        self._model = SentenceTransformer(model_name)
        self.dim = int(self._model.get_sentence_embedding_dimension())

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        arr = self._model.encode(list(texts), normalize_embeddings=False, show_progress_bar=False)
        return [list(map(float, v)) for v in arr]


_singleton: Optional[Embedder] = None


def get_embedder() -> Optional[Embedder]:
    """Return the cached embedder, or None if none is available.

    First call probes backends; subsequent calls return the same object.
    """
    global _singleton
    if _singleton is not None:
        return _singleton if _singleton.name != "none" else None
    backend = os.getenv("EMBEDDINGS_BACKEND", "").strip().lower()
    if backend == "none":
        return None

    tried: list[str] = []

    if backend in ("", "ollama"):
        ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        ollama_model = os.getenv("EMBEDDINGS_OLLAMA_MODEL", "nomic-embed-text")
        try:
            r = requests.post(
                f"{ollama_url}/api/embeddings",
                json={"model": ollama_model, "prompt": "probe"},
                timeout=3,
            )
            r.raise_for_status()
            if r.json().get("embedding"):
                _singleton = OllamaEmbedder(ollama_url, ollama_model)
                logger.info("embeddings backend: ollama (%s)", ollama_model)
                return _singleton
        except Exception as e:  # noqa: BLE001
            tried.append(f"ollama:{type(e).__name__}")

    if backend in ("", "sentence-transformers", "st"):
        st_model = os.getenv("EMBEDDINGS_ST_MODEL", "all-MiniLM-L6-v2")
        try:
            _singleton = SentenceTransformersEmbedder(st_model)
            logger.info("embeddings backend: sentence-transformers (%s)", st_model)
            return _singleton
        except Exception as e:  # noqa: BLE001
            tried.append(f"sentence-transformers:{type(e).__name__}")

    logger.info("no embeddings backend available (%s); memory.search will use FTS5", ", ".join(tried) or "none")
    return None


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def reset_for_tests() -> None:
    global _singleton
    _singleton = None
