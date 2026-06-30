"""Slack bot for ai_company orchestrator.

Socket Mode (no public URL needed). Single slash command:

    /ai-run <task>             → starts a workflow; bot replies in-thread

Then each checkpoint posts a Block Kit message in the same thread with
"✅ Approve" / "✋ Reject" buttons. Clicking resumes the graph.

Required env (only when you run the bot):
    SLACK_BOT_TOKEN=xoxb-...
    SLACK_APP_TOKEN=xapp-...        # Socket Mode app-level token

Run with:
    python -m communication.slack

Tokens are loaded by slack_bolt's App() from those env vars. slack_bolt +
slack_sdk are not in requirements.txt by default — install with:

    pip install slack-bolt slack-sdk
"""
from __future__ import annotations

import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from langgraph.types import Command

from orchestrator.graph import build_graph, new_workflow_id
from storage import queue as task_queue

logger = logging.getLogger(__name__)


# ---------- workflow runner (background thread per workflow) ----------


@dataclass
class _ActiveWorkflow:
    workflow_id: str
    channel: str
    thread_ts: str
    user: str
    last_pending: Optional[dict] = None  # interrupt payload waiting for human


_MAX_WORKFLOW_THREADS = int(os.getenv("SLACK_MAX_WORKFLOW_THREADS", "20"))


class WorkflowDispatcher:
    """Maps workflow_id → (channel, thread, user) and drives graph.stream calls."""

    def __init__(self, post_blocks_callable):
        # post_blocks_callable(channel, thread_ts, text, blocks): posts to Slack
        self._post = post_blocks_callable
        self._graph = build_graph()
        self._wf: Dict[str, _ActiveWorkflow] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=_MAX_WORKFLOW_THREADS,
                                        thread_name_prefix="slack-wf")

    def _config(self, wid: str) -> dict:
        return {"configurable": {"thread_id": wid}}

    def start_workflow(self, *, task: str, channel: str, thread_ts: str, user: str) -> str:
        wid = new_workflow_id()
        with self._lock:
            self._wf[wid] = _ActiveWorkflow(workflow_id=wid, channel=channel, thread_ts=thread_ts, user=user)
        self._pool.submit(self._run_until_interrupt, wid, {"task": task, "workflow_id": wid})
        return wid

    def resume(self, *, workflow_id: str, decision: dict) -> None:
        with self._lock:
            if workflow_id not in self._wf:
                logger.warning("slack: resume requested for unknown workflow %s", workflow_id)
                return
        self._pool.submit(self._run_until_interrupt, workflow_id, Command(resume=decision))

    def _run_until_interrupt(self, wid: str, payload: Any) -> None:
        with self._lock:
            wf = self._wf.get(wid)
        if wf is None:
            logger.warning("slack: _run_until_interrupt called for unknown wid %s", wid)
            return
        try:
            interrupted = None
            for ev in self._graph.stream(payload, config=self._config(wid)):
                if "__interrupt__" in ev:
                    interrupted = ev["__interrupt__"][0].value
                    break
            if interrupted:
                wf.last_pending = interrupted
                self._post(wf.channel, wf.thread_ts, *self._render_checkpoint(wid, interrupted))
                self._post_media(wf.channel, wf.thread_ts, wid, interrupted)
            else:
                self._post(wf.channel, wf.thread_ts, f"✅ workflow `{wid}` finished.", [])
                with self._lock:
                    self._wf.pop(wid, None)
        except Exception as e:  # noqa: BLE001
            logger.exception("slack workflow error")
            self._post(wf.channel, wf.thread_ts, f"❌ error in workflow `{wid}`: `{e}`", [])
            with self._lock:
                self._wf.pop(wid, None)

    def _post_media(self, channel: str, thread_ts: str, wid: str, payload: dict) -> None:
        """Best-effort: post a screenshot + voice note of the checkpoint. Failures are logged."""
        if os.getenv("AI_COMPANY_MEDIA", "1") in ("0", "false", "no"):
            return
        try:
            from . import media as _m

            try:
                png = _m.render_checkpoint_image(payload, wid)
                self._upload(channel, thread_ts, png, filename=f"{wid}.png", title=f"checkpoint {payload.get('kind','?')}")
            except Exception as e:  # noqa: BLE001
                logger.warning("slack image upload skipped: %s", e)
            try:
                audio, mime, _ = _m.synthesize_voice(_m._narration_text(payload))
                if audio:
                    self._upload(channel, thread_ts, audio, filename=f"{wid}.mp3", title="voice note")
            except Exception as e:  # noqa: BLE001
                logger.warning("slack voice upload skipped: %s", e)
        except Exception as e:  # noqa: BLE001
            logger.warning("media post failed: %s", e)

    # _upload is filled in by build_app() so test paths can stub it.
    def _upload(self, channel: str, thread_ts: str, data: bytes, *, filename: str, title: str) -> None:  # pragma: no cover
        logger.debug("slack _upload not configured; skipping %s", filename)

    # ---------- Block Kit rendering ----------

    @staticmethod
    def _render_checkpoint(wid: str, payload: dict) -> tuple[str, list]:
        kind = payload.get("kind", "?")
        if kind == "review_plan":
            plan = payload.get("plan") or []
            steps = "\n".join(f"*{i}.* {s.get('title','')} — files: `{', '.join(s.get('files') or []) or '(none)'}`"
                              for i, s in enumerate(plan, 1))
            text = f"*Plan ready* (workflow `{wid}`)\n```{payload.get('analysis','')[:600]}```\n{steps}"
        elif kind == "review_code":
            tr = payload.get("test_results") or {}
            files = ", ".join(c.get("path", "") for c in payload.get("code_changes", []) or [])
            text = (f"*Code applied* (workflow `{wid}`)  tests passed: `{tr.get('passed')}`\n"
                    f"files: `{files or '(none)'}`")
        elif kind == "review_commit":
            text = f"*Ready to commit* (workflow `{wid}`)\n```{payload.get('commit_message','')[:600]}```"
        else:
            text = f"*Checkpoint* (workflow `{wid}`)\n```{json.dumps(payload, indent=2, default=str)[:800]}```"

        blocks = [
            {"type": "section", "text": {"type": "mrkdwn", "text": text}},
            {"type": "actions", "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": "✅ Approve"},
                 "style": "primary", "action_id": "ai_company.approve", "value": wid},
                {"type": "button", "text": {"type": "plain_text", "text": "✋ Reject"},
                 "style": "danger", "action_id": "ai_company.reject", "value": wid},
            ]},
        ]
        return text, blocks


# ---------- Bot entry-point ----------


def build_app():
    """Construct the slack_bolt App. Imports slack_bolt lazily."""
    try:
        from slack_bolt import App
    except ImportError as e:
        raise RuntimeError("slack_bolt not installed. `pip install slack-bolt slack-sdk`") from e
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        raise RuntimeError("SLACK_BOT_TOKEN not set")
    app = App(token=token)

    def _post(channel: str, thread_ts: str, text: str, blocks: list) -> None:
        app.client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=text, blocks=blocks or None)

    dispatcher = WorkflowDispatcher(post_blocks_callable=_post)

    def _upload(channel: str, thread_ts: str, data: bytes, *, filename: str, title: str) -> None:
        # files_upload_v2 is the supported path in slack-sdk >= 3.20.
        app.client.files_upload_v2(
            channel=channel, thread_ts=thread_ts, content=data, filename=filename, title=title,
        )

    dispatcher._upload = _upload  # type: ignore[attr-defined]

    @app.command("/ai-run")
    def handle_run(ack, body, respond):
        ack()
        task = (body.get("text") or "").strip()
        if not task:
            respond("Usage: `/ai-run <task description>`")
            return
        channel = body["channel_id"]
        user = body["user_id"]
        # Open a thread by posting a starter message; subsequent posts go in that thread.
        starter = app.client.chat_postMessage(channel=channel, text=f"🟢 starting workflow for <@{user}>: _{task[:200]}_")
        thread_ts = starter["ts"]
        wid = dispatcher.start_workflow(task=task, channel=channel, thread_ts=thread_ts, user=user)
        app.client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=f"workflow_id: `{wid}`")
        task_queue.push(task, priority=0, workflow_id=wid, metadata={"source": "slack", "user": user, "channel": channel})

    def _check_approver(body) -> Optional[str]:
        """Return None if allowed, or an error message if not."""
        if not allowed_approvers:
            return None
        clicker = (body.get("user") or {}).get("id", "")
        if clicker in allowed_approvers:
            return None
        return clicker

    @app.action("ai_company.approve")
    def handle_approve(ack, body, action):
        ack()
        clicker = _check_approver(body)
        if clicker is not None:
            chan = (body.get("channel") or {}).get("id", "")
            try:
                app.client.chat_postEphemeral(
                    channel=chan, user=(body.get("user") or {}).get("id", ""),
                    text="⛔ Not on the approver allowlist (SLACK_ALLOWED_APPROVERS).",
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("postEphemeral failed: %s", e)
            return
        wid = action["value"]
        dispatcher.resume(workflow_id=wid, decision={"approved": True})

    @app.action("ai_company.reject")
    def handle_reject(ack, body, action):
        ack()
        clicker = _check_approver(body)
        if clicker is not None:
            chan = (body.get("channel") or {}).get("id", "")
            try:
                app.client.chat_postEphemeral(
                    channel=chan, user=(body.get("user") or {}).get("id", ""),
                    text="⛔ Not on the approver allowlist (SLACK_ALLOWED_APPROVERS).",
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("postEphemeral failed: %s", e)
            return
        wid = action["value"]
        dispatcher.resume(workflow_id=wid, decision={"approved": False, "reason": f"rejected by <@{body['user']['id']}>"})

    # ---------- passive ticket detector ----------
    # Listens to channel messages, asks the LLM whether each looks like a
    # coding task, posts a confirmation prompt before starting a workflow.
    # Activation:
    #   SLACK_AUTO_CHANNELS=C0123,C0456   comma-sep channel IDs we should
    #                                     auto-scan (without needing a mention)
    #   SLACK_AUTO_MENTIONS_ALWAYS=true   in any channel the bot is mentioned in,
    #                                     classify the message even if the channel
    #                                     is not on the allowlist
    auto_channels = {
        c.strip() for c in (os.getenv("SLACK_AUTO_CHANNELS", "").split(","))
        if c.strip()
    }
    mentions_always = os.getenv("SLACK_AUTO_MENTIONS_ALWAYS", "true").lower() in ("1", "true", "yes", "on")
    # SLACK_ALLOWED_APPROVERS=U01ABC,U02DEF — comma-separated Slack user IDs
    # allowed to click "Start workflow". Empty = no restriction.
    allowed_approvers = {
        u.strip() for u in (os.getenv("SLACK_ALLOWED_APPROVERS", "").split(","))
        if u.strip()
    }
    # SLACK_THREAD_CONTEXT_MAX_REPLIES=8 — top-level msg often vague; we pull
    # up to N replies from the same thread before classifying so the model
    # sees the actual ask.
    thread_max_replies = int(os.getenv("SLACK_THREAD_CONTEXT_MAX_REPLIES", "8"))

    bot_user_id_holder: dict[str, str] = {}

    def _bot_user_id() -> str:
        if "v" not in bot_user_id_holder:
            try:
                bot_user_id_holder["v"] = app.client.auth_test()["user_id"]
            except Exception as e:  # noqa: BLE001
                logger.warning("auth_test failed: %s", e)
                bot_user_id_holder["v"] = ""
        return bot_user_id_holder["v"]

    def _should_scan(event: dict) -> bool:
        # Skip bot/system messages, edits, thread replies.
        if event.get("subtype") in ("bot_message", "message_changed", "message_deleted",
                                    "channel_join", "channel_leave"):
            return False
        if event.get("bot_id"):
            return False
        if event.get("thread_ts") and event.get("thread_ts") != event.get("ts"):
            return False
        text = (event.get("text") or "").strip()
        if len(text) < 10:
            return False
        channel = event.get("channel", "")
        if channel in auto_channels:
            return True
        if mentions_always:
            uid = _bot_user_id()
            return bool(uid) and f"<@{uid}>" in text
        return False

    def _thread_context(channel: str, thread_ts: str, top_text: str) -> str:
        """Pull up to `thread_max_replies` reply texts from the same thread and
        return them concatenated with the top message. Top-level messages
        (thread_ts == ts) often read as vague ("can someone fix this?"); the
        actual ask usually lives in replies."""
        if thread_max_replies <= 0:
            return top_text
        try:
            res = app.client.conversations_replies(
                channel=channel, ts=thread_ts, limit=thread_max_replies + 1,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("conversations.replies failed: %s", e)
            return top_text
        msgs = res.get("messages") or []
        if len(msgs) <= 1:
            return top_text
        parts: list[str] = [top_text]
        bot_uid = _bot_user_id()
        for m in msgs[1:thread_max_replies + 1]:
            # Skip our own confirmation prompts; they'd leak into classification.
            if m.get("bot_id") or m.get("user") == bot_uid:
                continue
            t = (m.get("text") or "").strip()
            if t:
                parts.append(t)
        return "\n---\n".join(parts)

    @app.event("message")
    def handle_passive_message(event, say, client):
        if not _should_scan(event):
            return
        channel = event["channel"]
        ts = event["ts"]
        text = (event.get("text") or "").strip()
        # Strip bot mention from the text before classification so the model
        # judges the actual ask, not our own user id.
        uid = _bot_user_id()
        if uid:
            text = text.replace(f"<@{uid}>", "").strip()

        # Dedup: skip if we've already classified the same (channel, ts, text).
        # Survives bot restarts because the cache lives in SQLite.
        from storage import slack_tickets as _store
        if _store.already_seen(channel, ts, text):
            return

        # Pull thread context (replies) before classifying so the model sees
        # the actual ask, not just a vague top-level prompt.
        thread_ts = event.get("thread_ts") or ts
        classification_input = _thread_context(channel, thread_ts, text)

        try:
            from .ticket_detector import classify
            result = classify(classification_input)
        except Exception as e:  # noqa: BLE001
            logger.warning("ticket classify error: %s", e)
            return

        # Record the classification result either way so we don't re-classify
        # the same message after a restart.
        _store.mark_seen(channel, ts, text, was_ticket=result.ticket,
                         summary=result.summary)
        if not result.ticket:
            return

        pending_id = f"slk-{thread_ts}"
        _store.add_pending(
            pending_id,
            channel=channel, thread_ts=thread_ts,
            user=event.get("user", ""),
            task=result.summary or classification_input[:200],
            raw=classification_input,
        )
        blocks = [
            {"type": "section", "text": {"type": "mrkdwn",
                "text": (f"🎫 *Ticket detected* (confidence {result.confidence:.0%})\n"
                         f"> {result.summary}\n"
                         f"Start a workflow for this?")}},
            {"type": "actions", "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": "🟢 Start workflow"},
                 "style": "primary", "action_id": "ai_company.ticket_start", "value": pending_id},
                {"type": "button", "text": {"type": "plain_text", "text": "🟡 Not now"},
                 "action_id": "ai_company.ticket_skip", "value": pending_id},
            ]},
        ]
        app.client.chat_postMessage(channel=channel, thread_ts=thread_ts,
                                     text="Ticket detected — start workflow?", blocks=blocks)

    @app.action("ai_company.ticket_start")
    def handle_ticket_start(ack, body, action):
        ack()
        clicker = (body.get("user") or {}).get("id", "")
        # Approver gate: when SLACK_ALLOWED_APPROVERS is set, only listed
        # Slack user ids may start a workflow. Empty = open to anyone.
        if allowed_approvers and clicker not in allowed_approvers:
            chan = (body.get("channel") or {}).get("id", "")
            try:
                app.client.chat_postEphemeral(
                    channel=chan, user=clicker,
                    text="⛔ Not on the approver allowlist (SLACK_ALLOWED_APPROVERS).",
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("postEphemeral failed: %s", e)
            return
        from storage import slack_tickets as _store
        ticket = _store.pop_pending(action["value"])
        if not ticket:
            return
        wid = dispatcher.start_workflow(
            task=ticket.task, channel=ticket.channel,
            thread_ts=ticket.thread_ts, user=ticket.user,
        )
        app.client.chat_postMessage(
            channel=ticket.channel, thread_ts=ticket.thread_ts,
            text=f"▶ workflow `{wid}` started by <@{clicker}> for: _{ticket.task[:160]}_",
        )
        task_queue.push(ticket.task, priority=0, workflow_id=wid,
                        metadata={"source": "slack-ticket", "user": ticket.user,
                                  "approver": clicker, "channel": ticket.channel})

    @app.action("ai_company.ticket_skip")
    def handle_ticket_skip(ack, body, action):
        ack()
        from storage import slack_tickets as _store
        _store.pop_pending(action["value"])

    return app


def main() -> int:
    try:
        from slack_bolt.adapter.socket_mode import SocketModeHandler
    except ImportError:
        print("slack_bolt missing. Run: pip install slack-bolt slack-sdk")
        return 2
    app_token = os.getenv("SLACK_APP_TOKEN")
    if not app_token:
        print("SLACK_APP_TOKEN not set (Socket Mode app-level token; starts with xapp-)")
        return 2
    app = build_app()
    print("⚡ Slack bot running (Socket Mode). Ctrl-C to stop.")
    SocketModeHandler(app, app_token).start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
