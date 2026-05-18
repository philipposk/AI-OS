"""Telegram dispatcher tests. No real Telegram, no real LLM."""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Any, List

import pytest

from communication import telegram as tg
import orchestrator.nodes as nodes_mod
import tools.file_editor as file_editor_mod
from router.base import ChatResult


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_COMPANY_DB", str(tmp_path / "tg.sqlite"))
    monkeypatch.setenv("EMBEDDINGS_BACKEND", "none")


class StubRouter:
    def chat(self, messages, task_type="simple", model=None, max_tokens=1024, temperature=0.7, workflow_id=None):
        text = {
            "plan": '[{"title":"x","detail":"y","files":["a.py"]}]',
            "analyze": "analysis",
            "review": "review",
        }.get(task_type, "ok")
        return ChatResult(text=text, model="stub", provider="stub", prompt_tokens=1, completion_tokens=1)

    def chat_stream(self, *a, **k):
        raise RuntimeError("no stream in stub")


@pytest.fixture
def stub_router(monkeypatch, tmp_path):
    sr = StubRouter()
    monkeypatch.setattr(nodes_mod, "_router", sr)
    monkeypatch.setattr(file_editor_mod, "_router", sr)
    monkeypatch.setattr(file_editor_mod, "WORKING_TREE", tmp_path)
    (tmp_path / "a.py").write_text("orig\n", encoding="utf-8")
    monkeypatch.setattr(nodes_mod, "_get_stream_writer", lambda: None)
    return sr


def _make_loop():
    """Spin up an asyncio loop in a background thread (mimics PTB at runtime)."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    return loop, thread


def _stop_loop(loop):
    loop.call_soon_threadsafe(loop.stop)


def _wait_for(predicate, timeout=5.0, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_dispatcher_posts_checkpoint(stub_router):
    posts: List[tuple] = []

    async def fake_post(chat_id: int, text: str, kb):
        posts.append((chat_id, text, kb))

    loop, _ = _make_loop()
    try:
        d = tg.TelegramDispatcher(post=fake_post, loop=loop)
        wid = d.start_workflow(task="add version", chat_id=42, user="alice")
        assert _wait_for(lambda: posts)
        chat_id, text, kb = posts[-1]
        assert chat_id == 42
        assert "workflow" in text
        # Inline keyboard: 1 row, 2 buttons
        assert kb and len(kb) == 1 and len(kb[0]) == 2
        cb_values = {b["callback_data"] for b in kb[0]}
        assert cb_values == {f"aic:approve:{wid}", f"aic:reject:{wid}"}
    finally:
        _stop_loop(loop)


def test_dispatcher_approve_resumes(stub_router):
    posts: List[tuple] = []

    async def fake_post(chat_id, text, kb):
        posts.append((chat_id, text, kb))

    loop, _ = _make_loop()
    try:
        d = tg.TelegramDispatcher(post=fake_post, loop=loop)
        wid = d.start_workflow(task="x", chat_id=1, user="u")
        assert _wait_for(lambda: posts)
        d.resume(workflow_id=wid, decision={"approved": True})
        assert _wait_for(lambda: len(posts) >= 2, timeout=15)
    finally:
        _stop_loop(loop)


def test_dispatcher_reject_ends(stub_router):
    posts: List[tuple] = []

    async def fake_post(chat_id, text, kb):
        posts.append((chat_id, text, kb))

    loop, _ = _make_loop()
    try:
        d = tg.TelegramDispatcher(post=fake_post, loop=loop)
        wid = d.start_workflow(task="x", chat_id=1, user="u")
        assert _wait_for(lambda: posts)
        d.resume(workflow_id=wid, decision={"approved": False, "reason": "no"})
        assert _wait_for(lambda: any("finished" in p[1] for p in posts), timeout=5)
        assert wid not in d._wf
    finally:
        _stop_loop(loop)


def test_build_application_errors_without_lib(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "telegram", None)
    monkeypatch.setitem(sys.modules, "telegram.ext", None)
    with pytest.raises((RuntimeError, ImportError)):
        tg.build_application()


def test_render_checkpoint_review_plan_includes_steps():
    text, kb = tg.TelegramDispatcher._render_checkpoint("wf1", {
        "kind": "review_plan",
        "analysis": "thing",
        "plan": [{"title": "step 1", "detail": "d", "files": ["a.py"]}],
    })
    assert "step 1" in text
    assert "a.py" in text
    assert kb[0][0]["callback_data"] == "aic:approve:wf1"
