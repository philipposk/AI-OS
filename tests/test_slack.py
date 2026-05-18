"""Slack dispatcher tests. No real Slack; no real LLM."""
from __future__ import annotations

import time
from typing import Any

import pytest

from communication import slack as sl
import orchestrator.nodes as nodes_mod
import tools.file_editor as file_editor_mod
from router.base import ChatResult


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_COMPANY_DB", str(tmp_path / "slack.sqlite"))
    monkeypatch.setenv("EMBEDDINGS_BACKEND", "none")


class StubRouter:
    def __init__(self):
        self.calls = 0

    def chat(self, messages, task_type="simple", model=None, max_tokens=1024, temperature=0.7, workflow_id=None):
        self.calls += 1
        if task_type == "plan":
            text = '[{"title":"x","detail":"y","files":["a.py"]}]'
        elif task_type == "analyze":
            text = "analysis"
        elif task_type == "review":
            text = "review"
        else:
            text = "ok"
        return ChatResult(text=text, model="stub", provider="stub", prompt_tokens=1, completion_tokens=1)

    def chat_stream(self, *a, **k):
        # Pretend no streaming so nodes fall back to chat()
        raise RuntimeError("no stream in stub")


@pytest.fixture
def stub_router(monkeypatch, tmp_path):
    sr = StubRouter()
    monkeypatch.setattr(nodes_mod, "_router", sr)
    monkeypatch.setattr(file_editor_mod, "_router", sr)
    monkeypatch.setattr(file_editor_mod, "WORKING_TREE", tmp_path)
    (tmp_path / "a.py").write_text("orig\n", encoding="utf-8")
    # Force nodes to never see a stream writer
    monkeypatch.setattr(nodes_mod, "_get_stream_writer", lambda: None)
    return sr


def _wait_for(predicate, timeout=5.0, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_dispatcher_posts_plan_checkpoint(stub_router):
    posts: list[tuple] = []
    def fake_post(channel, thread_ts, text, blocks):
        posts.append((channel, thread_ts, text, blocks))

    d = sl.WorkflowDispatcher(fake_post)
    wid = d.start_workflow(task="add version flag", channel="C123", thread_ts="100.0", user="U1")
    assert _wait_for(lambda: posts)

    text, blocks = posts[-1][2], posts[-1][3]
    assert "workflow" in text
    # Block Kit with approve+reject buttons
    actions = [b for b in blocks if b.get("type") == "actions"][0]
    button_actions = {el["action_id"] for el in actions["elements"]}
    assert button_actions == {"ai_company.approve", "ai_company.reject"}
    # Buttons carry workflow_id as value
    vals = {el["value"] for el in actions["elements"]}
    assert vals == {wid}


def test_dispatcher_resume_advances_workflow(stub_router):
    posts: list[tuple] = []
    def fake_post(channel, thread_ts, text, blocks):
        posts.append((channel, thread_ts, text, blocks))

    d = sl.WorkflowDispatcher(fake_post)
    wid = d.start_workflow(task="add version flag", channel="C", thread_ts="1.0", user="U")
    assert _wait_for(lambda: posts)
    # First checkpoint is review_plan; approve → should reach checkpoint_commit
    # (tests path inside the workflow doesn't run real pytest because file_editor is stubbed
    # — the do_test node will subprocess-pytest; let it run, it produces something or fails;
    # either way the dispatcher should post a subsequent message)
    d.resume(workflow_id=wid, decision={"approved": True})
    assert _wait_for(lambda: len(posts) >= 2, timeout=15)


def test_dispatcher_reject_ends_workflow(stub_router):
    posts: list[tuple] = []
    def fake_post(channel, thread_ts, text, blocks):
        posts.append((channel, thread_ts, text, blocks))

    d = sl.WorkflowDispatcher(fake_post)
    wid = d.start_workflow(task="thing", channel="C", thread_ts="1.0", user="U")
    assert _wait_for(lambda: posts)
    d.resume(workflow_id=wid, decision={"approved": False, "reason": "no"})
    # Should post a "finished" message (since rejection routes to END)
    assert _wait_for(lambda: any("finished" in p[2] for p in posts), timeout=5)
    assert wid not in d._wf  # cleaned up


def test_render_checkpoint_handles_unknown_kind():
    text, blocks = sl.WorkflowDispatcher._render_checkpoint("wid-1", {"kind": "weird_thing", "x": 1})
    assert "weird_thing" in text or "wid-1" in text
    assert any(b.get("type") == "actions" for b in blocks)


def test_build_app_errors_if_slack_bolt_missing(monkeypatch):
    """Don't actually require slack_bolt to be installed; verify the import-guard works."""
    import sys
    monkeypatch.setitem(sys.modules, "slack_bolt", None)
    with pytest.raises((RuntimeError, ImportError)):
        sl.build_app()
