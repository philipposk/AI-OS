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

Passive ticket detection (optional):
    TELEGRAM_AUTO_DETECT=1        — scan all incoming text messages for tickets
    TELEGRAM_AUTO_SCAN_CHATS=    — comma-sep chat ids to scan (empty = all allowed)
    TICKET_CONFIDENCE_MIN=0.6     — minimum confidence to surface a ticket
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

    def __init__(
        self,
        post: Callable[[int, str, Optional[list]], Awaitable[None]],
        loop: asyncio.AbstractEventLoop,
        post_photo: Optional[Callable[[int, bytes, str], Awaitable[None]]] = None,
        post_audio: Optional[Callable[[int, bytes, str], Awaitable[None]]] = None,
    ):
        self._post = post
        self._post_photo = post_photo
        self._post_audio = post_audio
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
                self._post_media(wf.chat_id, wid, interrupted)
            else:
                self._post_blocking(wf.chat_id, f"✅ workflow `{wid}` finished.", None)
                with self._lock:
                    self._wf.pop(wid, None)
        except Exception as e:  # noqa: BLE001
            logger.exception("telegram workflow error")
            self._post_blocking(wf.chat_id, f"❌ error in workflow `{wid}`: `{e}`", None)
            with self._lock:
                self._wf.pop(wid, None)

    def _post_media(self, chat_id: int, wid: str, payload: dict) -> None:
        if os.getenv("AI_COMPANY_MEDIA", "1") in ("0", "false", "no"):
            return
        try:
            from . import media as _m

            if self._post_photo is not None:
                try:
                    png = _m.render_checkpoint_image(payload, wid)
                    fut = asyncio.run_coroutine_threadsafe(self._post_photo(chat_id, png, f"{wid}.png"), self._loop)
                    fut.result(timeout=30)
                except Exception as e:  # noqa: BLE001
                    logger.warning("telegram photo upload skipped: %s", e)
            if self._post_audio is not None:
                try:
                    audio, _mime, _ = _m.synthesize_voice(_m._narration_text(payload))
                    if audio:
                        fut = asyncio.run_coroutine_threadsafe(self._post_audio(chat_id, audio, f"{wid}.mp3"), self._loop)
                        fut.result(timeout=60)
                except Exception as e:  # noqa: BLE001
                    logger.warning("telegram audio upload skipped: %s", e)
        except Exception as e:  # noqa: BLE001
            logger.warning("telegram media post failed: %s", e)

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


def _make_post_photo(bot):
    from io import BytesIO

    async def post_photo(chat_id: int, data: bytes, filename: str) -> None:
        await bot.send_photo(chat_id=chat_id, photo=BytesIO(data), filename=filename)

    return post_photo


def _make_post_audio(bot):
    from io import BytesIO

    async def post_audio(chat_id: int, data: bytes, filename: str) -> None:
        # Use send_audio (mp3) — works in every client. send_voice requires opus
        # which needs ffmpeg, deferred.
        await bot.send_audio(chat_id=chat_id, audio=BytesIO(data), filename=filename, title="checkpoint voice")

    return post_audio


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
        state["dispatcher"] = TelegramDispatcher(
            post=_make_post(app.bot),
            loop=asyncio.get_running_loop(),
            post_photo=_make_post_photo(app.bot),
            post_audio=_make_post_audio(app.bot),
        )

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

    # ---------- passive ticket detection ----------

    def _auto_detect_on() -> bool:
        return os.getenv("TELEGRAM_AUTO_DETECT", "").strip().lower() in ("1", "true", "yes", "on")

    def _scan_chats() -> Set[int]:
        raw = os.getenv("TELEGRAM_AUTO_SCAN_CHATS", "").strip()
        if not raw:
            return set()
        return {int(x.strip()) for x in raw.split(",") if x.strip()}

    async def on_message(update, context):
        """Passive ticket scanner — runs on every text message."""
        msg = update.effective_message
        chat = update.effective_chat
        if msg is None or not msg.text:
            return
        # Allowlist checks
        if allowed and chat.id not in allowed:
            return
        scan_chats = _scan_chats()
        if scan_chats and chat.id not in scan_chats:
            return
        text = msg.text.strip()
        if not text:
            return
        # Dedup: reuse slack_tickets store (channel = str(chat.id), ts = str(msg.message_id))
        try:
            from storage import slack_tickets as st
            chan_key = str(chat.id)
            ts_key = str(msg.message_id)
            if st.already_seen(chan_key, ts_key, text):
                return
        except Exception as e:  # noqa: BLE001
            logger.warning("telegram dedup check failed: %s", e)
            return
        # Classify
        try:
            from communication.ticket_detector import classify
            result = classify(text)
        except Exception as e:  # noqa: BLE001
            logger.warning("telegram ticket classify failed: %s", e)
            return
        try:
            st.mark_seen(chan_key, ts_key, text, was_ticket=result.ticket, summary=result.summary)
        except Exception as e:  # noqa: BLE001
            logger.warning("telegram mark_seen failed: %s", e)
        if not result.ticket:
            return
        # Store as pending and send confirmation prompt
        user = update.effective_user
        username = user.username or str(user.id)
        pending_id = f"tg:{chat.id}:{msg.message_id}"
        try:
            st.add_pending(
                pending_id,
                channel=chan_key,
                thread_ts=ts_key,
                user=username,
                task=result.summary or text[:400],
                raw=text,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("telegram add_pending failed: %s", e)
            return
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Start", callback_data=f"aic:ticket_start:{pending_id}"),
            InlineKeyboardButton("❌ Skip",  callback_data=f"aic:ticket_skip:{pending_id}"),
        ]])
        await msg.reply_text(
            f"🎫 *Ticket detected* (confidence {result.confidence:.0%})\n_{result.summary[:300]}_\n\n"
            f"Start an AI workflow for this?",
            parse_mode="Markdown",
            reply_markup=kb,
        )

    async def on_callback(update, context):
        cq = update.callback_query
        await cq.answer()
        data = (cq.data or "").split(":")
        if not data or data[0] != "aic":
            return
        if len(data) == 3 and data[1] in ("approve", "reject"):
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
            return
        # Ticket start / skip — pending_id may contain colons (tg:chatid:msgid)
        if len(data) >= 3 and data[1] in ("ticket_start", "ticket_skip"):
            action = data[1]
            pending_id = ":".join(data[2:])
            try:
                from storage import slack_tickets as st
                ticket = st.pop_pending(pending_id)
            except Exception as e:  # noqa: BLE001
                logger.warning("telegram pop_pending failed: %s", e)
                ticket = None
            await cq.edit_message_reply_markup(reply_markup=None)
            if ticket is None:
                await context.bot.send_message(chat_id=cq.message.chat_id, text="⚠️ Ticket already handled or expired.")
                return
            if action == "ticket_skip":
                await context.bot.send_message(chat_id=cq.message.chat_id, text="⏭ Ticket skipped.")
                return
            # Start workflow
            d: TelegramDispatcher = state["dispatcher"]
            chat_id = int(ticket.channel)
            wid = d.start_workflow(task=ticket.task, chat_id=chat_id, user=ticket.user)
            await context.bot.send_message(
                chat_id=cq.message.chat_id,
                text=f"🟢 started workflow `{wid}` for _{ticket.task[:200]}_",
                parse_mode="Markdown",
            )

    from telegram.ext import MessageHandler, filters
    if _auto_detect_on():
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

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
