"""Workflow + project memory using SQLite FTS5.

Chose FTS5 over ChromaDB because:
- the Phase 0 cloud target is a £20/mo Oracle VM where chromadb +
  opentelemetry was the original break-point;
- FTS5 ships inside Python's stdlib sqlite3 — zero extra deps;
- keyword search is good enough for "remember what we did with this file
  yesterday"; if the user later needs true semantic search, they can swap
  this module for a ChromaDB-backed equivalent without changing callers.

Documents are stored with a free-form `kind`, optional `workflow_id`, and a
JSON `meta` blob.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from .db import connect

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_docs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    workflow_id TEXT,
    meta TEXT,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
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
    conn.executescript(_SCHEMA)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def add(text: str, *, kind: str = "note", workflow_id: Optional[str] = None, meta: Optional[dict] = None) -> int:
    with connect() as conn:
        _ensure(conn)
        cur = conn.execute(
            "INSERT INTO memory_docs (kind, workflow_id, meta, text, created_at) VALUES (?, ?, ?, ?, ?) RETURNING id",
            (kind, workflow_id, json.dumps(meta or {}), text, _now()),
        )
        return cur.fetchone()["id"]


def _quote_fts(query: str) -> str:
    # Wrap each non-empty token in quotes so FTS5 treats them literally.
    tokens = [t for t in query.split() if t]
    return " ".join(f'"{t.replace(chr(34), "")}"' for t in tokens) or '""'


def search(query: str, *, kind: Optional[str] = None, limit: int = 10) -> List[MemoryDoc]:
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
