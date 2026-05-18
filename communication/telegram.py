"""Telegram bot for ai_company.

Same shape as communication/slack.py: a WorkflowDispatcher drives the graph
in background threads, posting checkpoint cards with inline-keyboard
Approve / Reject buttons. Uses long polling so no public URL is required.

Required env (only when running the bot):
    TELEGRAM_BOT_TOKEN=123456:ABC-...

Run:
    pip install python-telegram-bot
    python -m communication.telegram

Allowlist (optional):
    TELEGRAM_ALLOWED_CHAT_IDS=12345,67890   # comma-separated chat ids
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional, Set

from langgraph.types import Command

from orchestrator.graph import build_graph, new_workflow_id
from storage import queue as task_queue

logger = logging.getLogger(__name__)


@dataclass
class _Active:
    workflow_id: str
    chat_id: int
    user: str


class TelegramDispatcher:
    """Runs workflows for a Telegram chat. The `post` callable sends a message
    with text + optional inline keyboard. It must be coroutine-safe — we call
    it from worker threads via run_coroutine_threadsafe.
    """

    def __init__(self, post: Callable[[int, str, Optional[list]], Awaitable[None]], loop: asyncio.AbstractEventLoop):
        self._post = post
        self._loop = loop
        self._graph = build_graph()
        self._wf: Dict[str, _Active] = {}
        self._lock = threading.Lock()

    def _config(self, wid: str) -> dict:
        return {"configurable": {"thread_id": wid}}

    def start_workflow(self, *, task: str, chat_id: int, user: str) -> str:
        wid = new_workflow_id()
        with self._lock:
            self._wf[wid] = _Active(workflow_id=wid, chat_id=chat_id, user=user)
        threading.Thread(target=self._run_until_interrupt, args=(wid, {"task": task, "workflow_id": wid}), daemon=True).start()
        return wid

    def resume(self, *, workflow_id: str, decision: dict) -> None:
        with self._lock:
            if workflow_id not in self._wf:
                logger.warning("telegram: resume requested for unknown workflow %s", workflow_id)
                return
        threading.Thread(target=self._run_until_interrupt, args=(workflow_id, Command(resume=decision)), daemon=True).start()

    def _post_blocking(self, chat_id: int, text: str, keyboard: Optional[list]) -> None:
        fut = asyncio.run_coroutine_threadsafe(self._post(chat_id, text, keyboard), self._loop)
        try:
            fut.result(timeout=15)
        except Exception as e:  # noqa: BLE001
            logger.warning("telegram post failed: %s", e)

    def _run_until_interrupt(self, wid: str, payload: Any) -> None:
        wf = self._wf[wid]
        try:
            interrupted = None
            for ev in self._graph.stream(payload, config=self._config(wid)):
                if "__interrupt__" in ev:
                    interrupted = ev["__interrupt__"][0].value
                    break
            if interrupted:
                text, kb = self._render_checkpoint(wid, interrupted)
                self._post_blocking(wf.chat_id, text, kb)
            else:
                self._post_blocking(wf.chat_id, f"✅ workflow `{wid}` finished.", None)
                with self._lock:
                    self._wf.pop(wid, None)
        except Exception as e:  # noqa: BLE001
            logger.exception("telegram workflow error")
            self._post_blocking(wf.chat_id, f"❌ error in workflow `{wid}`: `{e}`", None)
            with self._lock:
                self._wf.pop(wid, None)

    @staticmethod
    def _render_checkpoint(wid: str, payload: dict) -> tuple[str, list]:
        kind = payload.get("kind", "?")
        if kind == "review_plan":
            plan = payload.get("plan") or []
            steps = "\n".join(
                f"*{i}.* {s.get('title','')} — files: `{', '.join(s.get('files') or []) or '(none)'}`"
                for i, s in enumerate(plan, 1)
            )
            text = f"*Plan ready* (workflow `{wid}`)\n```\n{payload.get('analysis','')[:600]}\n```\n{steps}"
        elif kind == "review_code":
            tr = payload.get("test_results") or {}
            files = ", ".join(c.get("path", "") for c in payload.get("code_changes", []) or [])
            text = f"*Code applied* (workflow `{wid}`) tests passed: `{tr.get('passed')}`\nfiles: `{files or '(none)'}`"
        elif kind == "review_commit":
            text = f"*Ready to commit* (workflow `{wid}`)\n```\n{payload.get('commit_message','')[:600]}\n```"
        else:
            text = f"*Checkpoint* (workflow `{wid}`)\n```\n{json.dumps(payload, indent=2, default=str)[:800]}\n```"
        keyboard = [[
            {"text": "✅ Approve", "callback_data": f"aic:approve:{wid}"},
            {"text": "✋ Reject",  "callback_data": f"aic:reject:{wid}"},
        ]]
        return text, keyboard


# ---------- bot entry-point ----------


def _allowed_chats() -> Set[int]:
    raw = os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "").strip()
    if not raw:
        return set()
    return {int(x.strip()) for x in raw.split(",") if x.strip()}


def _make_post(bot):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    async def post(chat_id: int, text: str, keyboard: Optional[list]) -> None:
        markup = None
        if keyboard:
            markup = InlineKeyboardMarkup(
                [[InlineKeyboardButton(b["text"], callback_data=b["callback_data"]) for b in row] for row in keyboard]
            )
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=markup, parse_mode="Markdown")

    return post


def build_application():
    """Construct the python-telegram-bot Application. Lazy import."""
    try:
        from telegram.ext import Application, CommandHandler, CallbackQueryHandler
    except ImportError as e:
        raise RuntimeError("python-telegram-bot not installed. `pip install python-telegram-bot`") from e
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set")
    application = Application.builder().token(token).build()
    allowed = _allowed_chats()

    # Dispatcher will be wired once the event loop is running (during run_polling).
    state: dict = {"dispatcher": None}

    async def post_init(app):
        state["dispatcher"] = TelegramDispatcher(post=_make_post(app.bot), loop=asyncio.get_running_loop())

    application.post_init = post_init

    async def cmd_ai_run(update, context):
        chat = update.effective_chat
        if allowed and chat.id not in allowed:
            await update.message.reply_text("not authorised")
            return
        task = " ".join(context.args or []).strip()
        if not task:
            await update.message.reply_text("Usage: `/ai_run <task description>`", parse_mode="Markdown")
            return
        d: TelegramDispatcher = state["dispatcher"]
        user = update.effective_user.username or str(update.effective_user.id)
        wid = d.start_workflow(task=task, chat_id=chat.id, user=user)
        await update.message.reply_text(f"🟢 starting workflow `{wid}` for _{task[:200]}_", parse_mode="Markdown")
        task_queue.push(task, priority=0, workflow_id=wid, metadata={"source": "telegram", "user": user, "chat_id": chat.id})

    async def on_callback(update, context):
        cq = update.callback_query
        await cq.answer()
        data = (cq.data or "").split(":")
        if len(data) != 3 or data[0] != "aic":
            return
        _, action, wid = data
        d: TelegramDispatcher = state["dispatcher"]
        if action == "approve":
            d.resume(workflow_id=wid, decision={"approved": True})
            await cq.edit_message_reply_markup(reply_markup=None)
            await context.bot.send_message(chat_id=cq.message.chat_id, text=f"✅ approved `{wid}`", parse_mode="Markdown")
        elif action == "reject":
            d.resume(workflow_id=wid, decision={"approved": False, "reason": f"rejected by {cq.from_user.username or cq.from_user.id}"})
            await cq.edit_message_reply_markup(reply_markup=None)
            await context.bot.send_message(chat_id=cq.message.chat_id, text=f"✋ rejected `{wid}`", parse_mode="Markdown")

    application.add_handler(CommandHandler("ai_run", cmd_ai_run))
    application.add_handler(CallbackQueryHandler(on_callback, pattern=r"^aic:"))
    return application


def main() -> int:
    try:
        app = build_application()
    except RuntimeError as e:
        print(str(e))
        return 2
    print("⚡ Telegram bot running (long polling). Ctrl-C to stop.")
    app.run_polling()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
