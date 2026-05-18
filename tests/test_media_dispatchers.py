"""Dispatcher integration: do Slack + Telegram actually post media at checkpoints."""
from __future__ import annotations

import asyncio
import threading
import time
from typing import List

import pytest

from communication import slack as sl
from communication import telegram as tg
import orchestrator.nodes as nodes_mod
import tools.file_editor as file_editor_mod
from router.base import ChatResult


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_COMPANY_DB", str(tmp_path / "media.sqlite"))
    monkeypatch.setenv("EMBEDDINGS_BACKEND", "none")
    monkeypatch.setenv("AI_COMPANY_MEDIA", "1")


class StubRouter:
    def chat(self, messages, task_type="simple", model=None, max_tokens=1024, temperature=0.7, workflow_id=None):
        text = {"plan": '[{"title":"x","detail":"y","files":["a.py"]}]', "analyze": "analysis", "review": "review"}.get(task_type, "ok")
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


def _wait(pred, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred(): return True
        time.sleep(0.05)
    return False


# ---------- Slack ----------


def test_slack_dispatcher_uploads_image_and_voice(stub_router, monkeypatch):
    """Voice goes through edge-tts → stub it. Image goes through PIL → real call."""
    posts = []
    uploads = []

    def fake_post(channel, thread_ts, text, blocks):
        posts.append((channel, thread_ts, text, blocks))

    def fake_upload(channel, thread_ts, data, *, filename, title):
        uploads.append((filename, len(data), title))

    # Stub edge-tts so we get bytes without network.
    import sys, types
    class _FC:
        def __init__(self, *a, **k): pass
        async def save(self, path):
            open(path, "wb").write(b"FAKEMP3")
    monkeypatch.setitem(sys.modules, "edge_tts", types.SimpleNamespace(Communicate=_FC))

    d = sl.WorkflowDispatcher(fake_post)
    d._upload = fake_upload
    wid = d.start_workflow(task="add version flag", channel="C123", thread_ts="100.0", user="U1")
    assert _wait(lambda: posts and uploads)

    names = sorted(u[0] for u in uploads)
    assert any(n.endswith(".png") for n in names)
    assert any(n.endswith(".mp3") for n in names)
    # Image > a few hundred bytes; audio is the FAKEMP3 stub
    sizes = {n: s for n, s, _ in uploads}
    assert sizes[f"{wid}.png"] > 200
    assert sizes[f"{wid}.mp3"] == len(b"FAKEMP3")


def test_slack_media_disabled_by_env(stub_router, monkeypatch):
    monkeypatch.setenv("AI_COMPANY_MEDIA", "0")
    posts = []
    uploads = []
    d = sl.WorkflowDispatcher(lambda *a, **k: posts.append(a))
    d._upload = lambda *a, **k: uploads.append(k)
    d.start_workflow(task="x", channel="C", thread_ts="1", user="U")
    assert _wait(lambda: posts)
    # Wait a beat — media post happens after text post
    time.sleep(0.3)
    assert uploads == []


# ---------- Telegram ----------


def _loop():
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    return loop


def test_telegram_dispatcher_uploads_photo_and_audio(stub_router, monkeypatch):
    photos: List[bytes] = []
    audios: List[bytes] = []
    posts: List[tuple] = []

    async def fake_post(chat_id, text, kb):
        posts.append((chat_id, text, kb))
    async def fake_photo(chat_id, data, filename):
        photos.append(data)
    async def fake_audio(chat_id, data, filename):
        audios.append(data)

    import sys, types
    class _FC:
        def __init__(self, *a, **k): pass
        async def save(self, path):
            open(path, "wb").write(b"TGFAKEMP3")
    monkeypatch.setitem(sys.modules, "edge_tts", types.SimpleNamespace(Communicate=_FC))

    loop = _loop()
    try:
        d = tg.TelegramDispatcher(post=fake_post, loop=loop, post_photo=fake_photo, post_audio=fake_audio)
        d.start_workflow(task="x", chat_id=99, user="u")
        assert _wait(lambda: posts and photos and audios)
        assert photos[0].startswith(b"\x89PNG")
        assert audios[0] == b"TGFAKEMP3"
    finally:
        loop.call_soon_threadsafe(loop.stop)


def test_telegram_media_disabled_by_env(stub_router, monkeypatch):
    monkeypatch.setenv("AI_COMPANY_MEDIA", "false")
    photos = []; audios = []; posts = []

    async def fake_post(*a, **k): posts.append(a)
    async def fake_photo(*a, **k): photos.append(a)
    async def fake_audio(*a, **k): audios.append(a)

    loop = _loop()
    try:
        d = tg.TelegramDispatcher(post=fake_post, loop=loop, post_photo=fake_photo, post_audio=fake_audio)
        d.start_workflow(task="x", chat_id=1, user="u")
        assert _wait(lambda: posts)
        time.sleep(0.3)
        assert photos == [] and audios == []
    finally:
        loop.call_soon_threadsafe(loop.stop)
