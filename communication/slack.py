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
from dataclasses import dataclass
from typing import Any, Dict, Optional

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


class WorkflowDispatcher:
    """Maps workflow_id → (channel, thread, user) and drives graph.stream calls."""

    def __init__(self, post_blocks_callable):
        # post_blocks_callable(channel, thread_ts, text, blocks): posts to Slack
        self._post = post_blocks_callable
        self._graph = build_graph()
        self._wf: Dict[str, _ActiveWorkflow] = {}
        self._lock = threading.Lock()

    def _config(self, wid: str) -> dict:
        return {"configurable": {"thread_id": wid}}

    def start_workflow(self, *, task: str, channel: str, thread_ts: str, user: str) -> str:
        wid = new_workflow_id()
        with self._lock:
            self._wf[wid] = _ActiveWorkflow(workflow_id=wid, channel=channel, thread_ts=thread_ts, user=user)
        threading.Thread(target=self._run_until_interrupt, args=(wid, {"task": task, "workflow_id": wid}), daemon=True).start()
        return wid

    def resume(self, *, workflow_id: str, decision: dict) -> None:
        with self._lock:
            if workflow_id not in self._wf:
                logger.warning("slack: resume requested for unknown workflow %s", workflow_id)
                return
        threading.Thread(target=self._run_until_interrupt, args=(workflow_id, Command(resume=decision)), daemon=True).start()

    def _run_until_interrupt(self, wid: str, payload: Any) -> None:
        wf = self._wf[wid]
        try:
            interrupted = None
            for ev in self._graph.stream(payload, config=self._config(wid)):
                if "__interrupt__" in ev:
                    interrupted = ev["__interrupt__"][0].value
                    break
            if interrupted:
                wf.last_pending = interrupted
                self._post(wf.channel, wf.thread_ts, *self._render_checkpoint(wid, interrupted))
            else:
                self._post(wf.channel, wf.thread_ts, f"✅ workflow `{wid}` finished.", [])
                with self._lock:
                    self._wf.pop(wid, None)
        except Exception as e:  # noqa: BLE001
            logger.exception("slack workflow error")
            self._post(wf.channel, wf.thread_ts, f"❌ error in workflow `{wid}`: `{e}`", [])
            with self._lock:
                self._wf.pop(wid, None)

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

    @app.action("ai_company.approve")
    def handle_approve(ack, body, action):
        ack()
        wid = action["value"]
        dispatcher.resume(workflow_id=wid, decision={"approved": True})

    @app.action("ai_company.reject")
    def handle_reject(ack, body, action):
        ack()
        wid = action["value"]
        dispatcher.resume(workflow_id=wid, decision={"approved": False, "reason": f"rejected by <@{body['user']['id']}>"})

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
