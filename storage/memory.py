"""Workflow + project memory.

Two search paths:
  - Semantic via storage.embeddings.get_embedder() (Ollama → ST → none)
  - FTS5 keyword (always available)

Embeddings are stored as raw float32 bytes in BLOB column. Cosine similarity
is computed in Python — fine for <100k docs on a small VM. Swap to sqlite-vec
if you outgrow this.

Public API unchanged from earlier phases: add(), search(), for_workflow(),
count(). Each call to search() picks the path lazily — no global state
beyond the embedder singleton.
"""
from __future__ import annotations

import array
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from .db import connect
from .embeddings import cosine, get_embedder

logger = logging.getLogger(__name__)

_schema_created_for: Optional[str] = None  # tracks which DB path schema was created for
_schema_lock = __import__("threading").Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_docs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    workflow_id TEXT,
    meta TEXT,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    embedding BLOB,
    embedding_model TEXT
);
CREATE INDEX IF NOT EXISTS ix_memory_docs_kind ON memory_docs(kind);
CREATE INDEX IF NOT EXISTS ix_memory_docs_workflow ON memory_docs(workflow_id);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    text,
    content='memory_docs',
    content_rowid='id',
    tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS memory_ai AFTER INSERT ON memory_docs BEGIN
    INSERT INTO memory_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS memory_ad AFTER DELETE ON memory_docs BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, text) VALUES('delete', old.id, old.text);
END;
CREATE TRIGGER IF NOT EXISTS memory_au AFTER UPDATE ON memory_docs BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, text) VALUES('delete', old.id, old.text);
    INSERT INTO memory_fts(rowid, text) VALUES (new.id, new.text);
END;
"""


@dataclass
class MemoryDoc:
    id: int
    kind: str
    workflow_id: Optional[str]
    meta: dict
    text: str
    created_at: str
    score: Optional[float] = None

    @classmethod
    def from_row(cls, row, score: Optional[float] = None) -> "MemoryDoc":
        return cls(
            id=row["id"],
            kind=row["kind"],
            workflow_id=row["workflow_id"],
            meta=json.loads(row["meta"]) if row["meta"] else {},
            text=row["text"],
            created_at=row["created_at"],
            score=score,
        )


def _ensure(conn) -> None:
    global _schema_created_for
    import os as _os
    current_db = _os.getenv("AI_COMPANY_DB", "")
    if _schema_created_for != current_db:
        with _schema_lock:
            if _schema_created_for != current_db:
                conn.executescript(_SCHEMA)
                # Schema migration: pre-Phase-J databases lack the embedding columns.
                cols = {r["name"] for r in conn.execute("PRAGMA table_info(memory_docs)").fetchall()}
                if "embedding" not in cols:
                    conn.execute("ALTER TABLE memory_docs ADD COLUMN embedding BLOB")
                if "embedding_model" not in cols:
                    conn.execute("ALTER TABLE memory_docs ADD COLUMN embedding_model TEXT")
                _schema_created_for = current_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _vec_to_blob(v: List[float]) -> bytes:
    return array.array("f", v).tobytes()


def _blob_to_vec(b: bytes) -> List[float]:
    a = array.array("f")
    a.frombytes(b)
    return list(a)


def add(text: str, *, kind: str = "note", workflow_id: Optional[str] = None, meta: Optional[dict] = None) -> int:
    emb_blob: Optional[bytes] = None
    emb_model: Optional[str] = None
    embedder = get_embedder()
    if embedder is not None:
        try:
            vec = embedder.embed([text])[0]
            emb_blob = _vec_to_blob(vec)
            emb_model = embedder.name
        except Exception as e:  # noqa: BLE001
            logger.warning("memory.add: embedder failed (%s); storing without embedding", e)

    now = _now()
    with connect() as conn:
        _ensure(conn)
        cur = conn.execute(
            "INSERT INTO memory_docs (kind, workflow_id, meta, text, created_at, embedding, embedding_model) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (kind, workflow_id, json.dumps(meta or {}), text, now, emb_blob, emb_model),
        )
        doc_id = cur.fetchone()["id"]

    # Best-effort: mirror to Obsidian vault if configured.
    try:
        from . import obsidian as _obs
        _obs.mirror_note(doc_id=doc_id, kind=kind, text=text, workflow_id=workflow_id,
                         meta=meta or {}, created_at=now)
    except Exception as e:  # noqa: BLE001
        logger.warning("memory.add: obsidian mirror failed (%s); row still saved to db", e)

    return doc_id


def _quote_fts(query: str) -> str:
    tokens = [t for t in query.split() if t]
    return " ".join(f'"{t.replace(chr(34), "")}"' for t in tokens) or '""'


def _semantic_search(query: str, *, kind: Optional[str], limit: int) -> List[MemoryDoc]:
    embedder = get_embedder()
    if embedder is None:
        return []
    try:
        q_vec = embedder.embed([query])[0]
    except Exception as e:  # noqa: BLE001
        logger.warning("memory.search: embed query failed (%s); falling back to FTS", e)
        return []

    with connect() as conn:
        _ensure(conn)
        if kind:
            rows = conn.execute(
                "SELECT * FROM memory_docs WHERE kind = ? AND embedding IS NOT NULL", (kind,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM memory_docs WHERE embedding IS NOT NULL").fetchall()

    scored: list[tuple[float, MemoryDoc]] = []
    for row in rows:
        v = _blob_to_vec(row["embedding"])
        scored.append((cosine(q_vec, v), MemoryDoc.from_row(row)))
    scored.sort(key=lambda t: -t[0])
    out = []
    for score, doc in scored[:limit]:
        doc.score = score
        out.append(doc)
    return out


def _fts_search(query: str, *, kind: Optional[str], limit: int) -> List[MemoryDoc]:
    if not query.strip():
        return []
    fts_query = _quote_fts(query)
    with connect() as conn:
        _ensure(conn)
        if kind:
            rows = conn.execute(
                """
                SELECT d.*, bm25(memory_fts) AS score
                FROM memory_fts JOIN memory_docs d ON d.id = memory_fts.rowid
                WHERE memory_fts MATCH ? AND d.kind = ?
                ORDER BY score LIMIT ?
                """,
                (fts_query, kind, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT d.*, bm25(memory_fts) AS score
                FROM memory_fts JOIN memory_docs d ON d.id = memory_fts.rowid
                WHERE memory_fts MATCH ?
                ORDER BY score LIMIT ?
                """,
                (fts_query, limit),
            ).fetchall()
        return [MemoryDoc.from_row(r, score=r["score"]) for r in rows]


def search(query: str, *, kind: Optional[str] = None, limit: int = 10) -> List[MemoryDoc]:
    if not query.strip():
        return []
    # Semantic first; if it returns nothing (e.g. no embedded docs yet), fall back.
    sem = _semantic_search(query, kind=kind, limit=limit)
    if sem:
        return sem
    return _fts_search(query, kind=kind, limit=limit)


def for_workflow(workflow_id: str, limit: int = 50) -> List[MemoryDoc]:
    with connect() as conn:
        _ensure(conn)
        rows = conn.execute(
            "SELECT * FROM memory_docs WHERE workflow_id=? ORDER BY id DESC LIMIT ?",
            (workflow_id, limit),
        ).fetchall()
        return [MemoryDoc.from_row(r) for r in rows]


def count() -> int:
    with connect() as conn:
        _ensure(conn)
        return conn.execute("SELECT COUNT(*) FROM memory_docs").fetchone()[0]


def reembed_all(*, batch: int = 64) -> int:
    """Compute embeddings for any rows that lack them. Returns count updated."""
    embedder = get_embedder()
    if embedder is None:
        return 0
    updated = 0
    with connect() as conn:
        _ensure(conn)
        while True:
            rows = conn.execute(
                "SELECT id, text FROM memory_docs WHERE embedding IS NULL ORDER BY id LIMIT ?", (batch,)
            ).fetchall()
            if not rows:
                break
            texts = [r["text"] for r in rows]
            try:
                vecs = embedder.embed(texts)
            except Exception as e:  # noqa: BLE001
                logger.warning("reembed batch failed: %s", e)
                break
            for r, v in zip(rows, vecs):
                conn.execute(
                    "UPDATE memory_docs SET embedding=?, embedding_model=? WHERE id=?",
                    (_vec_to_blob(v), embedder.name, r["id"]),
                )
                updated += 1
    return updated
