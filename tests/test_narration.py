"""Narration text builder for dashboard TTS mode. UI rendering not unit-tested
(needs a Streamlit script context) — covered by the boot smoke test."""
from __future__ import annotations

import pytest


def _narr():
    from ui.dashboard import _narration_for
    return _narration_for


def test_narration_analyze_uses_first_chars():
    n = _narr()("do_analyze", {"analysis": "This is a thoughtful breakdown. " * 40})
    assert n.startswith("Analysis.")
    assert len(n) < 320  # truncated


def test_narration_plan_summarises_steps():
    n = _narr()("do_plan", {"plan": [
        {"title": "Add flag"}, {"title": "Update docs"}, {"title": "Write tests"},
    ]})
    assert n.startswith("Plan ready.")
    assert "3 steps" in n
    assert "Add flag" in n and "Update docs" in n


def test_narration_plan_singular():
    n = _narr()("do_plan", {"plan": [{"title": "Just one"}]})
    assert "1 step." in n


def test_narration_code_lists_files():
    n = _narr()("do_code", {"code_changes": [
        {"path": "cli.py"}, {"path": "tools/git_ops.py"},
    ]})
    assert "2 files" in n
    assert "cli.py" in n and "tools/git_ops.py" in n


def test_narration_test_pass_fail():
    assert _narr()("do_test", {"test_results": {"passed": True}}) == "Tests passed."
    assert _narr()("do_test", {"test_results": {"passed": False}}) == "Tests failed."


def test_narration_review_uses_summary():
    n = _narr()("do_review", {"review_summary": "Bumped versioning util."})
    assert n.startswith("Review.")
    assert "Bumped" in n


def test_narration_commit_with_sha():
    n = _narr()("do_commit", {"commit_sha": "deadbeefcafe"})
    assert "Committed." in n
    assert "deadbee" in n


def test_narration_empty_payload_returns_empty():
    assert _narr()("do_plan", {"plan": []}) == ""
    assert _narr()("do_code", {"code_changes": []}) == ""
    assert _narr()("unknown_node", {"anything": 1}) == ""
