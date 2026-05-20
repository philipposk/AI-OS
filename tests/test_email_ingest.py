"""Email ingest unit tests — no real IMAP, no real LLM."""
from __future__ import annotations

import email as _email
import email.policy
from unittest.mock import patch

import pytest

from communication.email_ingest import (
    EmailMessage,
    _decode_header,
    _extract_body,
    process_message,
)


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_COMPANY_DB", str(tmp_path / "email.sqlite"))
    monkeypatch.setenv("EMBEDDINGS_BACKEND", "none")
    monkeypatch.setenv("EMAIL_IMAP_USER", "bot@example.com")
    monkeypatch.delenv("EMAIL_ALLOWED_SENDERS", raising=False)
    monkeypatch.delenv("TICKET_PERSIST_DAYS", raising=False)
    monkeypatch.setenv("TICKET_CONFIDENCE_MIN", "0.6")


# ---------- helpers ----------

def _make_msg(msg_id="<abc@x>", sender="alice@x.com",
              subject="Fix login", body="Please fix the login bug asap") -> EmailMessage:
    return EmailMessage(message_id=msg_id, sender=sender, subject=subject, body=body)


def _fake_classify(ticket=True, confidence=0.9, summary="Fix login bug"):
    class R:
        def __init__(self):
            self.ticket = ticket
            self.summary = summary
            self.confidence = confidence
            self.raw = ""
    return lambda text, workflow_id=None: R()


# ---------- _decode_header ----------

def test_decode_header_ascii():
    assert _decode_header("Hello World") == "Hello World"


def test_decode_header_encoded():
    # RFC-2047 encoded UTF-8
    encoded = "=?utf-8?b?SGVsbG8gV29ybGQ=?="   # "Hello World"
    assert _decode_header(encoded) == "Hello World"


# ---------- _extract_body ----------

def test_extract_body_simple():
    raw = b"From: a@b.com\r\nSubject: hi\r\nContent-Type: text/plain\r\n\r\nHello"
    msg = _email.message_from_bytes(raw, policy=email.policy.default)
    body = _extract_body(msg)
    assert "Hello" in body


def test_extract_body_multipart():
    raw = (
        b"From: a@b.com\r\n"
        b"MIME-Version: 1.0\r\n"
        b"Content-Type: multipart/alternative; boundary=bound\r\n\r\n"
        b"--bound\r\n"
        b"Content-Type: text/plain\r\n\r\n"
        b"plain text\r\n"
        b"--bound\r\n"
        b"Content-Type: text/html\r\n\r\n"
        b"<b>html</b>\r\n"
        b"--bound--\r\n"
    )
    msg = _email.message_from_bytes(raw, policy=email.policy.default)
    body = _extract_body(msg)
    assert "plain text" in body


# ---------- process_message — ticket detected ----------

def test_process_message_ticket(monkeypatch):
    import communication.ticket_detector as td
    monkeypatch.setattr(td, "classify", _fake_classify(ticket=True, confidence=0.9))

    msg = _make_msg()
    result = process_message(msg)
    assert result is True

    from storage import slack_tickets as st
    pending = st.list_pending()
    assert any("abc@x" in p.pending_id for p in pending)


def test_process_message_non_ticket(monkeypatch):
    import communication.ticket_detector as td
    monkeypatch.setattr(td, "classify", _fake_classify(ticket=False, confidence=0.1))

    msg = _make_msg(msg_id="<non@x>")
    result = process_message(msg)
    assert result is False


def test_process_message_low_confidence(monkeypatch):
    import communication.ticket_detector as td
    monkeypatch.setattr(td, "classify", _fake_classify(ticket=True, confidence=0.3))

    msg = _make_msg(msg_id="<lowconf@x>")
    result = process_message(msg)
    assert result is False   # below TICKET_CONFIDENCE_MIN=0.6


def test_process_message_dedup(monkeypatch):
    import communication.ticket_detector as td
    monkeypatch.setattr(td, "classify", _fake_classify())

    msg = _make_msg(msg_id="<dup@x>")
    assert process_message(msg) is True
    # Second call: already_seen → False
    assert process_message(msg) is False


def test_process_message_sender_allowlist(monkeypatch):
    monkeypatch.setenv("EMAIL_ALLOWED_SENDERS", "trusted@example.com")
    import communication.ticket_detector as td
    monkeypatch.setattr(td, "classify", _fake_classify())

    # Not in allowlist — should be skipped
    msg = _make_msg(msg_id="<notallowed@x>", sender="stranger@evil.com")
    result = process_message(msg)
    assert result is False


def test_process_message_sender_allowed(monkeypatch):
    monkeypatch.setenv("EMAIL_ALLOWED_SENDERS", "alice@x.com")
    import communication.ticket_detector as td
    monkeypatch.setattr(td, "classify", _fake_classify())

    msg = _make_msg(msg_id="<allowed@x>", sender="alice@x.com")
    result = process_message(msg)
    assert result is True


# ---------- pending_id format ----------

def test_pending_id_uses_message_id(monkeypatch):
    """pending_id must embed the Message-ID for traceability."""
    import communication.ticket_detector as td
    monkeypatch.setattr(td, "classify", _fake_classify(ticket=True, confidence=0.9))
    msg = _make_msg(msg_id="<unique-id-xyz@host>")
    process_message(msg)

    from storage import slack_tickets as st
    pending = st.list_pending()
    ids = [p.pending_id for p in pending]
    assert any("unique-id-xyz" in pid for pid in ids)
