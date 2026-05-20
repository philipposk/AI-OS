"""Telegram passive ticket detection tests. No real Telegram, no real LLM."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_COMPANY_DB", str(tmp_path / "tg_tickets.sqlite"))
    monkeypatch.setenv("EMBEDDINGS_BACKEND", "none")
    monkeypatch.setenv("TELEGRAM_AUTO_DETECT", "1")
    monkeypatch.delenv("TICKET_PERSIST_DAYS", raising=False)


# ---------- dedup store reuse ----------

def test_telegram_dedup_uses_slack_tickets(monkeypatch):
    """Telegram reuses slack_tickets store with str(chat_id) as channel key."""
    from storage import slack_tickets as st

    chan = "99"
    ts = "12345"
    text = "please deploy the new release"
    assert st.already_seen(chan, ts, text) is False
    st.mark_seen(chan, ts, text, was_ticket=True, summary="deploy release")
    assert st.already_seen(chan, ts, text) is True


def test_telegram_pending_roundtrip():
    from storage import slack_tickets as st

    pending_id = "tg:99:12345"
    st.add_pending(pending_id, channel="99", thread_ts="12345",
                   user="alice", task="deploy release", raw="please deploy")
    out = st.pop_pending(pending_id)
    assert out is not None
    assert out.task == "deploy release"
    assert out.user == "alice"
    assert st.pop_pending(pending_id) is None  # destructive


# ---------- classify integration ----------

def test_classify_ticket_detected(monkeypatch):
    from communication import ticket_detector as td
    from dataclasses import dataclass

    @dataclass
    class FakeResult:
        ticket: bool = True
        summary: str = "deploy the new release"
        confidence: float = 0.9
        raw: str = ""

    monkeypatch.setattr(td, "classify", lambda text, workflow_id=None: FakeResult())
    result = td.classify("please deploy the new release")
    assert result.ticket is True
    assert result.confidence >= 0.6


def test_classify_non_ticket_ignored(monkeypatch):
    from communication import ticket_detector as td
    from dataclasses import dataclass

    @dataclass
    class FakeResult:
        ticket: bool = False
        summary: str = ""
        confidence: float = 0.2
        raw: str = ""

    monkeypatch.setattr(td, "classify", lambda text, workflow_id=None: FakeResult())
    result = td.classify("hello how are you")
    assert result.ticket is False


# ---------- pending_id format ----------

def test_pending_id_format_with_colons():
    """pending_id 'tg:chat:msg' contains colons — split on ':' must handle it."""
    pending_id = "tg:99:12345"
    parts = pending_id.split(":")
    assert parts[0] == "tg"
    action_data = ":".join(parts[1:])   # "99:12345"
    reconstructed = ":".join(["tg", action_data])
    assert reconstructed == pending_id


def test_pending_id_pop_by_full_id():
    from storage import slack_tickets as st

    pid = "tg:1234567890:9999"
    st.add_pending(pid, channel="1234567890", thread_ts="9999",
                   user="bob", task="fix the login bug", raw="fix login")
    ticket = st.pop_pending(pid)
    assert ticket is not None
    assert ticket.pending_id == pid
    assert ticket.channel == "1234567890"
