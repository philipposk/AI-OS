"""Crew message bus — SQLite-backed pub/sub.

Every post() inserts a row into `crew_messages` so the dashboard's "Crew
transcript" tab can replay an old workflow's deliberation. The in-memory
subscriber list is best-effort (same-process only) — multi-process readers
should poll `tail(workflow_id, after_id)` instead.

Schema:
    crew_messages(id, ts, workflow_id, role, content, meta_json)

Order is `id ASC`. `id` is the cursor a tail() reader passes back.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from storage.db import connect

_SCHEMA = """
CREATE TABLE IF NOT EXISTS crew_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    meta_json TEXT
);
CREATE INDEX IF NOT EXISTS ix_crew_messages_workflow ON crew_messages(workflow_id);
"""


@dataclass
class Message:
    id: int
    ts: str
    workflow_id: str
    role: str
    content: str
    meta: Dict[str, Any]


def _ensure(conn) -> None:
    conn.executescript(_SCHEMA)


class MessageBus:
    """In-process bus with persistent backing. Thread-safe enough for the
    coordinator's parallel role calls.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: List[Callable[[Message], None]] = []

    def post(self, *, workflow_id: str, role: str, content: str,
             meta: Optional[Dict[str, Any]] = None) -> Message:
        ts = datetime.now(timezone.utc).isoformat()
        meta_json = json.dumps(meta or {}, default=str)
        with connect() as conn:
            _ensure(conn)
            cur = conn.execute(
                "INSERT INTO crew_messages (ts, workflow_id, role, content, meta_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (ts, workflow_id, role, content, meta_json),
            )
            msg_id = cur.lastrowid
        msg = Message(id=msg_id, ts=ts, workflow_id=workflow_id, role=role,
                      content=content, meta=meta or {})
        with self._lock:
            subs = list(self._subs)
        for cb in subs:
            try:
                cb(msg)
            except Exception:  # noqa: BLE001
                # Subscriber problems must never break a publish.
                pass
        return msg

    def subscribe(self, cb: Callable[[Message], None]) -> None:
        with self._lock:
            self._subs.append(cb)

    def unsubscribe(self, cb: Callable[[Message], None]) -> None:
        with self._lock:
            try:
                self._subs.remove(cb)
            except ValueError:
                pass

    @staticmethod
    def transcript(workflow_id: str) -> List[Message]:
        with connect() as conn:
            _ensure(conn)
            rows = conn.execute(
                "SELECT * FROM crew_messages WHERE workflow_id=? ORDER BY id ASC",
                (workflow_id,),
            ).fetchall()
        return [Message(
            id=r["id"], ts=r["ts"], workflow_id=r["workflow_id"],
            role=r["role"], content=r["content"],
            meta=json.loads(r["meta_json"] or "{}"),
        ) for r in rows]

    @staticmethod
    def tail(workflow_id: str, after_id: int = 0, limit: int = 200) -> List[Message]:
        """Cursor-based read for live dashboards. Empty result = caught up."""
        with connect() as conn:
            _ensure(conn)
            rows = conn.execute(
                "SELECT * FROM crew_messages WHERE workflow_id=? AND id>? "
                "ORDER BY id ASC LIMIT ?",
                (workflow_id, after_id, limit),
            ).fetchall()
        return [Message(
            id=r["id"], ts=r["ts"], workflow_id=r["workflow_id"],
            role=r["role"], content=r["content"],
            meta=json.loads(r["meta_json"] or "{}"),
        ) for r in rows]


_bus_singleton: Optional[MessageBus] = None


def get_bus() -> MessageBus:
    global _bus_singleton
    if _bus_singleton is None:
        _bus_singleton = MessageBus()
    return _bus_singleton
