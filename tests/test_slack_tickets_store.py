"""Slack ticket store: dedup cache + persistent pending tickets + vacuum."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture(autouse=True)
def sandbox_db(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_COMPANY_DB", str(tmp_path / "test.sqlite"))
    monkeypatch.delenv("TICKET_PERSIST_DAYS", raising=False)
    yield


def test_already_seen_false_until_marked():
    from storage import slack_tickets as st

    assert st.already_seen("C1", "1.0", "fix login") is False
    st.mark_seen("C1", "1.0", "fix login", was_ticket=True, summary="x")
    assert st.already_seen("C1", "1.0", "fix login") is True
    # Different text on the same ts is a different signature.
    assert st.already_seen("C1", "1.0", "different text") is False


def test_mark_seen_idempotent():
    from storage import slack_tickets as st

    st.mark_seen("C1", "1.0", "t", was_ticket=False)
    st.mark_seen("C1", "1.0", "t", was_ticket=True, summary="update")
    # Second call shouldn't raise (INSERT OR REPLACE).
    assert st.already_seen("C1", "1.0", "t") is True


def test_pending_roundtrip():
    from storage import slack_tickets as st

    st.add_pending("p1", channel="C1", thread_ts="1.1", user="U1",
                   task="do thing", raw="raw text")
    out = st.pop_pending("p1")
    assert out is not None
    assert out.task == "do thing"
    assert out.user == "U1"
    # Pop is destructive.
    assert st.pop_pending("p1") is None


def test_pending_missing_returns_none():
    from storage import slack_tickets as st

    assert st.pop_pending("nope") is None


def test_list_pending_orders_recent_first():
    from storage import slack_tickets as st

    st.add_pending("a", channel="C", thread_ts="1", user="U", task="A")
    st.add_pending("b", channel="C", thread_ts="2", user="U", task="B")
    out = st.list_pending()
    ids = [p.pending_id for p in out]
    # b was inserted second -> most recent created_at -> first.
    assert ids[:2] == ["b", "a"]


def test_vacuum_drops_old_rows(monkeypatch):
    from storage import slack_tickets as st
    from storage.db import connect

    monkeypatch.setenv("TICKET_PERSIST_DAYS", "1")

    # Manually insert a stale row dated 5 days ago.
    stale_ts = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    with connect() as conn:
        conn.executescript(st._SCHEMA)
        conn.execute(
            "INSERT INTO slack_pending (pending_id, channel, thread_ts, user, task, raw, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("stale", "C", "0", "U", "old", "", stale_ts),
        )
    # Trigger vacuum via list_pending().
    assert all(p.pending_id != "stale" for p in st.list_pending())


def test_sig_is_text_sensitive():
    from storage import slack_tickets as st

    a = st._sig("C", "1", "hello")
    b = st._sig("C", "1", "hello")
    c = st._sig("C", "1", "hello!")
    assert a == b
    assert a != c
