"""SQLite-backed persistence for the Slack ticket-detection bot.

Two tables:

  slack_seen_msgs   — dedup cache: hash of (channel, ts, text) for every
                      message we've already classified. Same message-id
                      re-delivered (edits with identical text, retries,
                      restart-replay) skips a fresh classification.
  slack_pending     — confirmation prompts the bot posted but the user
                      hasn't clicked Start/Skip on. Survives restart.

Both tables auto-expire rows older than TICKET_PERSIST_DAYS (default 14)
on next access via _vacuum_old().
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .db import connect

_SCHEMA = """
CREATE TABLE IF NOT EXISTS slack_seen_msgs (
    sig TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    ts TEXT NOT NULL,
    classified_at TEXT NOT NULL,
    was_ticket INTEGER NOT NULL,
    summary TEXT
);
CREATE INDEX IF NOT EXISTS ix_slack_seen_classified_at ON slack_seen_msgs(classified_at);

CREATE TABLE IF NOT EXISTS slack_pending (
    pending_id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    thread_ts TEXT NOT NULL,
    user TEXT NOT NULL,
    task TEXT NOT NULL,
    raw TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_slack_pending_created_at ON slack_pending(created_at);
"""


@dataclass
class PendingTicket:
    pending_id: str
    channel: str
    thread_ts: str
    user: str
    task: str
    raw: str
    created_at: str


def _ensure(conn) -> None:
    conn.executescript(_SCHEMA)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _retention_days() -> int:
    try:
        return max(1, int(os.getenv("TICKET_PERSIST_DAYS", "14")))
    except ValueError:
        return 14


def _vacuum_old(conn) -> None:
    cutoff = time.time() - _retention_days() * 86400
    cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
    conn.execute("DELETE FROM slack_seen_msgs WHERE classified_at < ?", (cutoff_iso,))
    conn.execute("DELETE FROM slack_pending WHERE created_at < ?", (cutoff_iso,))


def _sig(channel: str, ts: str, text: str) -> str:
    h = hashlib.sha1()
    h.update(channel.encode())
    h.update(b"\x1f")
    h.update(ts.encode())
    h.update(b"\x1f")
    h.update(text.encode("utf-8"))
    return h.hexdigest()


def already_seen(channel: str, ts: str, text: str) -> bool:
    """Returns True if (channel, ts, text) was already classified. Cheap dedup
    so flaky edits / retry deliveries don't burn LLM calls.
    """
    sig = _sig(channel, ts, text)
    with connect() as conn:
        _ensure(conn)
        _vacuum_old(conn)
        row = conn.execute("SELECT 1 FROM slack_seen_msgs WHERE sig=?", (sig,)).fetchone()
    return row is not None


def mark_seen(channel: str, ts: str, text: str, *, was_ticket: bool,
              summary: Optional[str] = None) -> None:
    sig = _sig(channel, ts, text)
    with connect() as conn:
        _ensure(conn)
        conn.execute(
            "INSERT OR REPLACE INTO slack_seen_msgs "
            "(sig, channel, ts, classified_at, was_ticket, summary) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (sig, channel, ts, _now(), 1 if was_ticket else 0, summary),
        )


def add_pending(pending_id: str, *, channel: str, thread_ts: str, user: str,
                task: str, raw: str = "") -> None:
    with connect() as conn:
        _ensure(conn)
        conn.execute(
            "INSERT OR REPLACE INTO slack_pending "
            "(pending_id, channel, thread_ts, user, task, raw, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (pending_id, channel, thread_ts, user, task, raw, _now()),
        )


def pop_pending(pending_id: str) -> Optional[PendingTicket]:
    """Atomic get-and-delete. Returns None if missing."""
    with connect() as conn:
        _ensure(conn)
        _vacuum_old(conn)
        row = conn.execute(
            "SELECT * FROM slack_pending WHERE pending_id=?", (pending_id,)
        ).fetchone()
        if not row:
            return None
        conn.execute("DELETE FROM slack_pending WHERE pending_id=?", (pending_id,))
    return PendingTicket(
        pending_id=row["pending_id"], channel=row["channel"],
        thread_ts=row["thread_ts"], user=row["user"], task=row["task"],
        raw=row["raw"] or "", created_at=row["created_at"],
    )


def list_pending(limit: int = 50) -> list[PendingTicket]:
    with connect() as conn:
        _ensure(conn)
        _vacuum_old(conn)
        rows = conn.execute(
            "SELECT * FROM slack_pending ORDER BY created_at DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    return [PendingTicket(
        pending_id=r["pending_id"], channel=r["channel"],
        thread_ts=r["thread_ts"], user=r["user"], task=r["task"],
        raw=r["raw"] or "", created_at=r["created_at"],
    ) for r in rows]
