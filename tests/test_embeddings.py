"""Embeddings + semantic memory. No network."""
from __future__ import annotations

import math
from typing import List, Sequence

import pytest

from storage import memory as mem
from storage import embeddings as emb_mod


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_COMPANY_DB", str(tmp_path / "emb.sqlite"))
    monkeypatch.setenv("EMBEDDINGS_BACKEND", "none")
    emb_mod.reset_for_tests()


class _FakeEmbedder(emb_mod.Embedder):
    """Trivial deterministic embedder: a 3-dim vec based on keyword presence."""

    name = "fake"
    dim = 3

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        out = []
        for t in texts:
            t = t.lower()
            out.append([
                1.0 if "auth" in t else 0.0,
                1.0 if "db" in t or "database" in t else 0.0,
                1.0 if "ui" in t or "dashboard" in t else 0.0,
            ])
        return out


def _install_fake(monkeypatch):
    fake = _FakeEmbedder()
    emb_mod._singleton = fake
    return fake


# ---------- embeddings core ----------


def test_cosine_basic():
    assert emb_mod.cosine([1, 0, 0], [1, 0, 0]) == pytest.approx(1.0)
    assert emb_mod.cosine([1, 0, 0], [0, 1, 0]) == pytest.approx(0.0)
    assert emb_mod.cosine([1, 0, 0], [-1, 0, 0]) == pytest.approx(-1.0)
    assert emb_mod.cosine([0, 0, 0], [1, 1, 1]) == 0.0  # zero-vec safe


def test_get_embedder_returns_none_when_backend_forced_off():
    assert emb_mod.get_embedder() is None


# ---------- memory: backend = none → FTS5 path ----------


def test_memory_falls_back_to_fts_when_no_embedder():
    mem.add("alpha auth handler", kind="note")
    mem.add("blue ui refresh", kind="note")
    mem.add("green database migration", kind="note")
    hits = mem.search("auth")
    assert hits and "auth" in hits[0].text.lower()


# ---------- memory: with fake embedder → semantic path ----------


def test_memory_semantic_ranks_by_cosine(monkeypatch):
    _install_fake(monkeypatch)
    mem.add("fixed bug in auth middleware", kind="review")
    mem.add("rewrote db migration with new schema", kind="review")
    mem.add("updated ui dashboard layout", kind="review")
    mem.add("scratchpad with nothing useful", kind="note")

    # Query that's strongly "auth" → expect auth doc first
    hits = mem.search("auth fix needed")
    assert hits
    assert "auth" in hits[0].text
    assert hits[0].score == pytest.approx(1.0)


def test_memory_semantic_falls_back_to_fts_if_no_embedded_rows(monkeypatch):
    # Add row without an embedder first.
    mem.add("first row stored before embeddings", kind="note")
    # Then install embedder. Old row has no embedding → semantic returns empty
    # for "first row" → FTS picks up the keyword match.
    _install_fake(monkeypatch)
    hits = mem.search("first row")
    assert hits and "first row" in hits[0].text


def test_reembed_all_backfills_missing(monkeypatch):
    mem.add("doc one for backfill", kind="note")
    mem.add("doc two for backfill", kind="note")
    _install_fake(monkeypatch)
    n = mem.reembed_all()
    assert n == 2
    # After backfill, semantic returns hits with score 0 (fake embedder gives all-zero for these strings)
    from storage.db import connect
    with connect() as conn:
        rows = conn.execute("SELECT embedding, embedding_model FROM memory_docs").fetchall()
    assert all(r["embedding"] is not None for r in rows)
    assert {r["embedding_model"] for r in rows} == {"fake"}


# ---------- migration: tables created with extra columns idempotently ----------


def test_schema_migration_idempotent(monkeypatch):
    # Two consecutive add() calls must not raise even though _ensure() is run twice.
    mem.add("first", kind="note")
    mem.add("second", kind="note")
    assert mem.count() == 2
