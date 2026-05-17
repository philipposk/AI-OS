"""End-to-end graph tests with a stub router. No network."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any, List

import pytest
from langgraph.types import Command

from router.base import ChatResult
import orchestrator.nodes as nodes_mod
import tools.git_ops as git_ops_mod


class StubRouter:
    """Minimal router that returns canned text per task_type."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.plan_payload = [
            {"title": "Add --version flag", "detail": "Wire argparse --version", "files": ["cli.py"]},
        ]

    def chat(self, messages, task_type="simple", model=None, max_tokens=1024, temperature=0.7, workflow_id=None):
        self.calls.append((task_type, model, len(messages)))
        if task_type == "analyze":
            text = "Task wants a --version flag on cli.py. Risk: low."
        elif task_type == "plan":
            text = json.dumps(self.plan_payload)
        elif task_type == "review":
            text = "Added --version flag. cli.py touched. Tests passed."
        else:
            text = "ok"
        return ChatResult(text=text, model=model or "stub", provider="stub", prompt_tokens=1, completion_tokens=1)


@pytest.fixture(autouse=True)
def stub_router(monkeypatch):
    sr = StubRouter()
    monkeypatch.setattr(nodes_mod, "_router", sr)
    return sr


@pytest.fixture
def fake_tests_pass(monkeypatch):
    def fake_run(cmd, capture_output, text, timeout):
        return subprocess.CompletedProcess(cmd, 0, stdout="9 passed", stderr="")
    monkeypatch.setattr(nodes_mod.subprocess, "run", fake_run)


@pytest.fixture
def fake_tests_fail(monkeypatch):
    def fake_run(cmd, capture_output, text, timeout):
        return subprocess.CompletedProcess(cmd, 1, stdout="1 failed", stderr="AssertionError")
    monkeypatch.setattr(nodes_mod.subprocess, "run", fake_run)


@pytest.fixture
def fake_commit(monkeypatch):
    seen = {}
    def _commit(message, paths=None, cwd=None):
        seen["message"] = message
        seen["paths"] = list(paths or [])
        return "deadbeefcafe"
    monkeypatch.setattr(git_ops_mod, "commit_changes", _commit)
    # Also patch the import in the orchestrator nodes (it imports at call time).
    return seen


def _drive(graph, payload, config, interrupts: List[Any]):
    """Drive the graph, replying to interrupts from the front of the list. Returns final state."""
    current = payload
    while True:
        interrupted = False
        for ev in graph.stream(current, config=config):
            if "__interrupt__" in ev:
                if not interrupts:
                    raise AssertionError(f"unexpected interrupt: {ev['__interrupt__'][0].value}")
                reply = interrupts.pop(0)
                current = Command(resume=reply)
                interrupted = True
                break
        if not interrupted:
            break
    return graph.get_state(config).values


def test_happy_path_through_all_checkpoints(fake_tests_pass, fake_commit):
    from orchestrator.graph import build_graph

    g = build_graph()
    cfg = {"configurable": {"thread_id": "wf-happy"}}
    final = _drive(
        g,
        {"task": "Add --version flag to cli.py", "workflow_id": "wf-happy"},
        cfg,
        interrupts=[
            {"approved": True},                                          # checkpoint_plan
            # tests pass → skip checkpoint_code → review → checkpoint_commit
            {"approved": True, "commit_message": "Add --version flag"},  # checkpoint_commit
        ],
    )
    # Plan was parsed
    assert final["plan"] == [
        {"title": "Add --version flag", "detail": "Wire argparse --version", "files": ["cli.py"]}
    ]
    # Code node produced a stub change (since tools.file_editor is a stub)
    # because the plan had files=['cli.py']
    assert any(c["path"] == "cli.py" for c in final["code_changes"])
    # Test ran and passed
    assert final["test_results"]["passed"] is True
    # Review summary generated
    assert "version" in final["review_summary"].lower() or "added" in final["review_summary"].lower()
    # Commit happened
    assert final["commit_sha"] == "deadbeefcafe"
    assert fake_commit["message"] == "Add --version flag"


def test_plan_rejection_ends_workflow(fake_tests_pass, fake_commit):
    from orchestrator.graph import build_graph

    g = build_graph()
    cfg = {"configurable": {"thread_id": "wf-reject"}}
    final = _drive(
        g,
        {"task": "do something", "workflow_id": "wf-reject"},
        cfg,
        interrupts=[{"approved": False, "reason": "wrong direction"}],
    )
    # Should never have reached code/test/commit
    assert "code_changes" not in final or not final["code_changes"]
    assert "commit_sha" not in final or final.get("commit_sha") is None
    assert final["approved"] is False
    assert final["rejection_reason"] == "wrong direction"


def test_failing_tests_route_to_human_after_retries(fake_tests_fail, fake_commit, monkeypatch):
    """After MAX_TEST_RETRIES the graph should escalate to checkpoint_code (not loop forever)."""
    from orchestrator.graph import MAX_TEST_RETRIES, build_graph

    g = build_graph()
    cfg = {"configurable": {"thread_id": "wf-fail"}}

    final = _drive(
        g,
        {"task": "do something risky", "workflow_id": "wf-fail"},
        cfg,
        interrupts=[
            {"approved": True},                                # checkpoint_plan
            {"approved": False, "reason": "stop, tests fail"}, # checkpoint_code (after retries)
        ],
    )
    assert final["test_results"]["passed"] is False
    assert (final.get("retries") or 0) >= MAX_TEST_RETRIES
    # Rejected at checkpoint_code → no commit
    assert final.get("commit_sha") in (None, "")
