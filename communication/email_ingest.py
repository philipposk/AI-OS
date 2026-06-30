"""Email / IMAP passive ticket ingestion.

Polls an IMAP mailbox for unseen messages, classifies each one via
`ticket_detector.classify()`, and surfaces detected tickets as pending items
in the slack_tickets store (channel key = mailbox address, ts key = Message-ID).

Confirmation flow options (env-selectable):
  EMAIL_CONFIRM=reply  — send a reply to the sender asking them to confirm
                          (requires SMTP settings, see below)
  EMAIL_CONFIRM=none   — add to pending silently; operator approves via
                          `cli.py slack list / approve`

Required env:
    EMAIL_IMAP_HOST        IMAP server hostname (e.g. imap.gmail.com)
    EMAIL_IMAP_PORT        IMAP port (default 993)
    EMAIL_IMAP_USER        Login username / email address
    EMAIL_IMAP_PASSWORD    App password or OAuth token
    EMAIL_IMAP_FOLDER      Mailbox folder to watch (default INBOX)
    EMAIL_IMAP_POLL_SEC    Poll interval in seconds (default 60)

SMTP (only needed when EMAIL_CONFIRM=reply):
    EMAIL_SMTP_HOST        SMTP server hostname (e.g. smtp.gmail.com)
    EMAIL_SMTP_PORT        SMTP port (default 587)
    EMAIL_SMTP_USER        SMTP login (defaults to EMAIL_IMAP_USER)
    EMAIL_SMTP_PASSWORD    SMTP password (defaults to EMAIL_IMAP_PASSWORD)

Optional filters:
    EMAIL_ALLOWED_SENDERS  Comma-separated allowed sender addresses (empty = all)
    TICKET_CONFIDENCE_MIN  Minimum classify() confidence to surface (default 0.6)

Run the poll loop (blocking):
    python -m communication.email_ingest
"""
from __future__ import annotations

import email as _email
import email.header
import email.policy
import imaplib
import logging
import os
import smtplib
import time
from dataclasses import dataclass
from email.mime.text import MIMEText
from typing import Optional

logger = logging.getLogger(__name__)


# ---------- config helpers ----------

def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _imap_host() -> str: return _env("EMAIL_IMAP_HOST")
def _imap_port() -> int:
    try: return int(_env("EMAIL_IMAP_PORT", "993"))
    except ValueError: return 993
def _imap_user() -> str: return _env("EMAIL_IMAP_USER")
def _imap_password() -> str: return _env("EMAIL_IMAP_PASSWORD")
def _imap_folder() -> str: return _env("EMAIL_IMAP_FOLDER", "INBOX")
def _poll_interval() -> int:
    try: return max(10, int(_env("EMAIL_IMAP_POLL_SEC", "60")))
    except ValueError: return 60

def _smtp_host() -> str: return _env("EMAIL_SMTP_HOST")
def _smtp_port() -> int:
    try: return int(_env("EMAIL_SMTP_PORT", "587"))
    except ValueError: return 587
def _smtp_user() -> str: return _env("EMAIL_SMTP_USER") or _imap_user()
def _smtp_password() -> str: return _env("EMAIL_SMTP_PASSWORD") or _imap_password()

def _confirm_mode() -> str:
    return _env("EMAIL_CONFIRM", "none").lower()  # "reply" | "none"

def _allowed_senders() -> set[str]:
    raw = _env("EMAIL_ALLOWED_SENDERS")
    if not raw:
        return set()
    return {s.strip().lower() for s in raw.split(",") if s.strip()}

def _confidence_min() -> float:
    try: return float(_env("TICKET_CONFIDENCE_MIN", "0.6"))
    except ValueError: return 0.6


# ---------- data model ----------

@dataclass
class EmailMessage:
    message_id: str     # RFC-2822 Message-ID header (used as dedup key)
    sender: str
    subject: str
    body: str
    raw_bytes: bytes = b""


# ---------- IMAP helpers ----------

def _decode_header(value: str) -> str:
    """Decode RFC-2047 encoded words in header values."""
    parts = []
    for segment, charset in email.header.decode_header(value):
        if isinstance(segment, bytes):
            parts.append(segment.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(segment)
    return "".join(parts)


def _connect() -> imaplib.IMAP4_SSL:
    conn = imaplib.IMAP4_SSL(_imap_host(), _imap_port())
    conn.login(_imap_user(), _imap_password())
    return conn


def _fetch_unseen(conn: imaplib.IMAP4_SSL) -> list[EmailMessage]:
    """Select folder and return all UNSEEN messages."""
    conn.select(_imap_folder())
    _typ, data = conn.search(None, "UNSEEN")
    if not data or not data[0]:
        return []
    uids = data[0].split()
    messages: list[EmailMessage] = []
    for uid in uids:
        try:
            _t, raw = conn.fetch(uid, "(RFC822)")
            if not raw or not raw[0]:
                continue
            raw_bytes = raw[0][1]
            msg = _email.message_from_bytes(raw_bytes, policy=email.policy.default)
            msg_id = (msg.get("Message-ID") or "").strip()
            sender = _decode_header(msg.get("From") or "")
            subject = _decode_header(msg.get("Subject") or "")
            body = _extract_body(msg)
            if not msg_id:
                msg_id = f"uid:{uid.decode()}"
            messages.append(EmailMessage(
                message_id=msg_id,
                sender=sender,
                subject=subject,
                body=body,
                raw_bytes=raw_bytes,
            ))
        except Exception as e:  # noqa: BLE001
            logger.warning("email fetch uid %s failed: %s", uid, e)
    return messages


def _extract_body(msg) -> str:
    """Best-effort plain-text extraction."""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get("Content-Disposition") or "")
            if ct == "text/plain" and "attachment" not in cd:
                try:
                    return part.get_content()
                except Exception:  # noqa: BLE001
                    return part.get_payload(decode=True).decode("utf-8", errors="replace")
        return ""
    try:
        return msg.get_content()
    except Exception:  # noqa: BLE001
        payload = msg.get_payload(decode=True)
        return payload.decode("utf-8", errors="replace") if payload else ""


# ---------- SMTP confirm reply ----------

def _send_confirm_reply(to_addr: str, subject: str, pending_id: str, summary: str) -> None:
    """Send a reply email asking the sender to confirm the ticket."""
    host = _smtp_host()
    if not host:
        logger.warning("EMAIL_SMTP_HOST not set; skipping confirm reply")
        return
    body = (
        f"ai_company detected a potential task in your message:\n\n"
        f"  {summary}\n\n"
        f"To start an AI workflow, reply with: CONFIRM {pending_id}\n"
        f"To skip, reply with: SKIP {pending_id}\n\n"
        f"(Or use `cli.py slack list/approve/skip` in the terminal.)"
    )
    mime = MIMEText(body, "plain", "utf-8")
    mime["From"] = _smtp_user()
    mime["To"] = to_addr
    mime["Subject"] = f"Re: {subject} [ticket detected]"
    try:
        with smtplib.SMTP(_smtp_host(), _smtp_port()) as s:
            s.starttls()
            s.login(_smtp_user(), _smtp_password())
            s.send_message(mime)
        logger.info("confirm reply sent to %s for pending %s", to_addr, pending_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("SMTP reply to %s failed: %s", to_addr, e)


# ---------- processing pipeline ----------

def process_message(msg: EmailMessage, *, workflow_id: Optional[str] = None) -> bool:
    """Classify one email. Returns True if surfaced as a pending ticket."""
    from storage import slack_tickets as st

    # Sender allowlist
    allowed = _allowed_senders()
    sender_lower = msg.sender.lower()
    if allowed and not any(a in sender_lower for a in allowed):
        logger.debug("email from %s not in allowlist; skipping", msg.sender)
        return False

    # Dedup: channel = mailbox user, ts = message_id
    chan_key = _imap_user()
    ts_key = msg.message_id
    text = f"Subject: {msg.subject}\n\n{msg.body[:1200]}"

    if st.already_seen(chan_key, ts_key, text):
        logger.debug("email %s already seen; skipping", msg.message_id)
        return False

    # Classify
    try:
        from communication.ticket_detector import classify
        result = classify(text, workflow_id=workflow_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("email classify failed for %s: %s", msg.message_id, e)
        st.mark_seen(chan_key, ts_key, text, was_ticket=False)
        return False

    st.mark_seen(chan_key, ts_key, text, was_ticket=result.ticket, summary=result.summary)

    if not result.ticket or result.confidence < _confidence_min():
        logger.debug("email %s: not a ticket (ticket=%s, conf=%.2f)",
                     msg.message_id, result.ticket, result.confidence)
        return False

    # Store as pending
    pending_id = f"email:{msg.message_id[:80]}"
    task = result.summary or msg.subject or text[:200]
    try:
        st.add_pending(
            pending_id,
            channel=chan_key,
            thread_ts=ts_key,
            user=msg.sender,
            task=task,
            raw=text,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("email add_pending failed: %s", e)
        return False

    logger.info("ticket detected in email from %s: %r (conf=%.0f%%)",
                msg.sender, task[:100], result.confidence * 100)

    # Confirmation
    if _confirm_mode() == "reply":
        _send_confirm_reply(
            to_addr=msg.sender,
            subject=msg.subject,
            pending_id=pending_id,
            summary=result.summary or task,
        )
    else:
        logger.info("pending ticket %s — approve via `cli.py slack approve %s`",
                    pending_id, pending_id)

    return True


# ---------- poll loop ----------

def poll_once(*, workflow_id: Optional[str] = None) -> int:
    """Connect, fetch unseen, classify each. Returns count of tickets surfaced."""
    if not _imap_host():
        raise RuntimeError("EMAIL_IMAP_HOST not set")
    conn = _connect()
    try:
        messages = _fetch_unseen(conn)
    finally:
        try:
            conn.logout()
        except Exception:  # noqa: BLE001
            pass
    count = 0
    for msg in messages:
        try:
            if process_message(msg, workflow_id=workflow_id):
                count += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("process_message failed for %s: %s", msg.message_id, e)
    return count


def run_poll_loop(*, workflow_id: Optional[str] = None) -> None:  # pragma: no cover
    """Blocking poll loop with exponential backoff on consecutive errors. Ctrl-C to stop."""
    interval = _poll_interval()
    logger.info("email ingest starting; polling %s every %ds", _imap_folder(), interval)
    consecutive_failures = 0
    max_backoff = min(interval * 8, 3600)
    while True:
        try:
            n = poll_once(workflow_id=workflow_id)
            if n:
                logger.info("surfaced %d ticket(s) from email", n)
            consecutive_failures = 0
            time.sleep(interval)
        except Exception as e:  # noqa: BLE001
            consecutive_failures += 1
            backoff = min(interval * (2 ** (consecutive_failures - 1)), max_backoff)
            logger.warning("email poll failed (%d consecutive): %s; backing off %ds", consecutive_failures, e, backoff)
            time.sleep(backoff)


def main() -> int:  # pragma: no cover
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        run_poll_loop()
    except KeyboardInterrupt:
        print("\nstopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
