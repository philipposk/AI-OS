"""Phase R: tests for tools/git_ops gh wrappers + git_log/git_revert + parse_issue_refs.

All `gh`/`git` calls are monkey-patched. No real subprocess invocations.
"""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest


def _cp(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


# ---------- parse_issue_refs ----------


def test_parse_issue_refs_url_and_hash():
    from tools.git_ops import parse_issue_refs

    text = (
        "Fix bug in #42 — see https://github.com/acme/widget/pull/99 "
        "also issue https://github.com/acme/widget/issues/100 and #42 again, "
        "and ignore path/with/#50 ish things."
    )
    refs = parse_issue_refs(text)
    # 99 and 100 from URLs, 42 from #42, dedup the second #42.
    assert refs[:3] == [99, 100, 42]
    assert refs.count(42) == 1


def test_parse_issue_refs_empty():
    from tools.git_ops import parse_issue_refs

    assert parse_issue_refs("no refs here") == []


# ---------- gh wrappers ----------


def test_gh_available_true(monkeypatch):
    monkeypatch.setattr("tools.git_ops.shutil.which", lambda _: "/usr/local/bin/gh")
    from tools.git_ops import gh_available

    assert gh_available() is True


def test_gh_available_false(monkeypatch):
    monkeypatch.setattr("tools.git_ops.shutil.which", lambda _: None)
    from tools.git_ops import gh_available

    assert gh_available() is False


def test_gh_pr_view_parses_json(monkeypatch):
    monkeypatch.setattr("tools.git_ops.shutil.which", lambda _: "/x/gh")
    payload = {"number": 7, "title": "T", "state": "OPEN", "body": "B"}
    monkeypatch.setattr("tools.git_ops._gh", lambda *a, **k: _cp(stdout=json.dumps(payload)))
    from tools.git_ops import gh_pr_view

    assert gh_pr_view(7) == payload


def test_gh_pr_view_returns_none_when_gh_missing(monkeypatch):
    monkeypatch.setattr("tools.git_ops.shutil.which", lambda _: None)
    from tools.git_ops import gh_pr_view

    assert gh_pr_view(1) is None


def test_gh_pr_list_handles_non_list(monkeypatch):
    monkeypatch.setattr("tools.git_ops.shutil.which", lambda _: "/x/gh")
    monkeypatch.setattr("tools.git_ops._gh", lambda *a, **k: _cp(stdout="{}"))  # obj, not list
    from tools.git_ops import gh_pr_list

    assert gh_pr_list() == []


def test_gh_issue_view_failure_returns_none(monkeypatch):
    monkeypatch.setattr("tools.git_ops.shutil.which", lambda _: "/x/gh")
    monkeypatch.setattr("tools.git_ops._gh", lambda *a, **k: _cp(returncode=1, stderr="not found"))
    from tools.git_ops import gh_issue_view

    assert gh_issue_view(404) is None


def test_gh_repo_default_branch(monkeypatch):
    monkeypatch.setattr("tools.git_ops.shutil.which", lambda _: "/x/gh")
    monkeypatch.setattr(
        "tools.git_ops._gh",
        lambda *a, **k: _cp(stdout=json.dumps({"defaultBranchRef": {"name": "main"}})),
    )
    from tools.git_ops import gh_repo_default_branch

    assert gh_repo_default_branch() == "main"


# ---------- gh_context_for_task ----------


def test_gh_context_for_task_returns_pr_block(monkeypatch):
    monkeypatch.setattr("tools.git_ops.shutil.which", lambda _: "/x/gh")
    calls: list[list[str]] = []

    def fake_gh(*args, **kwargs):
        calls.append(list(args))
        # First arg = "pr", second = "view"  → PR returns valid JSON.
        if args[:2] == ("pr", "view"):
            return _cp(
                stdout=json.dumps(
                    {
                        "number": 42,
                        "title": "Fix the thing",
                        "state": "OPEN",
                        "body": "Full body here.",
                        "headRefName": "feat/x",
                        "baseRefName": "main",
                        "url": "https://github.com/a/b/pull/42",
                        "labels": [{"name": "bug"}],
                    }
                )
            )
        return _cp(returncode=1)

    monkeypatch.setattr("tools.git_ops._gh", fake_gh)
    from tools.git_ops import gh_context_for_task

    out = gh_context_for_task("Please fix #42 today")
    assert "[PR #42]" in out
    assert "Fix the thing" in out
    assert "bug" in out
    assert "Full body here." in out


def test_gh_context_for_task_no_refs(monkeypatch):
    monkeypatch.setattr("tools.git_ops.shutil.which", lambda _: "/x/gh")
    from tools.git_ops import gh_context_for_task

    assert gh_context_for_task("nothing relevant") == ""


def test_gh_context_for_task_no_gh_binary(monkeypatch):
    monkeypatch.setattr("tools.git_ops.shutil.which", lambda _: None)
    from tools.git_ops import gh_context_for_task

    assert gh_context_for_task("see #1") == ""


def test_gh_context_for_task_falls_back_to_issue(monkeypatch):
    monkeypatch.setattr("tools.git_ops.shutil.which", lambda _: "/x/gh")

    def fake_gh(*args, **kwargs):
        if args[:2] == ("pr", "view"):
            return _cp(returncode=1)  # not a PR
        if args[:2] == ("issue", "view"):
            return _cp(
                stdout=json.dumps(
                    {
                        "number": 9,
                        "title": "Issue title",
                        "state": "OPEN",
                        "body": "body",
                        "url": "https://github.com/a/b/issues/9",
                        "labels": [],
                    }
                )
            )
        return _cp(returncode=1)

    monkeypatch.setattr("tools.git_ops._gh", fake_gh)
    from tools.git_ops import gh_context_for_task

    out = gh_context_for_task("see #9")
    assert "[Issue #9]" in out
    assert "Issue title" in out


# ---------- git_log / git_revert ----------


def test_git_log_parses_records(monkeypatch):
    sep = "\x1f"
    rows = [
        sep.join(["aaaaa", "aaaa", "first", "alice", "2026-05-18T01:00:00+00:00"]),
        sep.join(["bbbbb", "bbbb", "second", "bob", "2026-05-18T02:00:00+00:00"]),
    ]
    monkeypatch.setattr("tools.git_ops._git", lambda *a, **k: _cp(stdout="\n".join(rows)))
    from tools.git_ops import git_log

    out = git_log(limit=5)
    assert len(out) == 2
    assert out[0]["sha"] == "aaaaa"
    assert out[0]["short"] == "aaaa"
    assert out[0]["subject"] == "first"
    assert out[1]["author"] == "bob"


def test_git_log_skips_malformed_rows(monkeypatch):
    rows = ["only-two\x1fparts", "\x1f".join(["s", "h", "subj", "a", "d"])]
    monkeypatch.setattr("tools.git_ops._git", lambda *a, **k: _cp(stdout="\n".join(rows)))
    from tools.git_ops import git_log

    out = git_log()
    assert len(out) == 1
    assert out[0]["sha"] == "s"


def test_git_revert_returns_new_head(monkeypatch):
    calls: list[tuple] = []

    def fake_git(*args, **kwargs):
        calls.append(args)
        if args[0] == "revert":
            return _cp()  # ok
        if args[0] == "rev-parse":
            return _cp(stdout="newsha\n")
        return _cp()

    monkeypatch.setattr("tools.git_ops._git", fake_git)
    from tools.git_ops import git_revert

    assert git_revert("oldsha") == "newsha"
    assert calls[0][:3] == ("revert", "--no-edit", "oldsha")


def test_git_revert_raises_on_failure(monkeypatch):
    monkeypatch.setattr(
        "tools.git_ops._git",
        lambda *a, **k: _cp(returncode=1, stderr="merge conflict"),
    )
    from tools.git_ops import git_revert

    with pytest.raises(RuntimeError, match="merge conflict"):
        git_revert("oldsha")


# ---------- commit_changes empty-paths guard ----------


def test_commit_changes_uses_git_add_all_when_paths_none(monkeypatch):
    """commit_changes(paths=None) must run 'git add -A', not 'git add -- '."""
    captured: list[tuple] = []

    def fake_git(*args, **kwargs):
        captured.append(args)
        if args[0] == "commit":
            return _cp()
        if args[0] == "rev-parse":
            return _cp(stdout="deadbeef\n")
        return _cp()

    monkeypatch.setattr("tools.git_ops._git", fake_git)
    from tools.git_ops import commit_changes

    commit_changes("msg", paths=None)
    add_calls = [c for c in captured if c[0] == "add"]
    assert len(add_calls) == 1
    assert add_calls[0] == ("add", "-A"), f"Expected git add -A, got {add_calls[0]}"


def test_commit_changes_uses_specific_paths_when_given(monkeypatch):
    """commit_changes(paths=['a.py']) must run 'git add -- a.py'."""
    captured: list[tuple] = []

    def fake_git(*args, **kwargs):
        captured.append(args)
        if args[0] == "commit":
            return _cp()
        if args[0] == "rev-parse":
            return _cp(stdout="cafebabe\n")
        return _cp()

    monkeypatch.setattr("tools.git_ops._git", fake_git)
    from tools.git_ops import commit_changes

    commit_changes("msg", paths=["a.py", "b.py"])
    add_calls = [c for c in captured if c[0] == "add"]
    assert len(add_calls) == 1
    assert add_calls[0] == ("add", "--", "a.py", "b.py")


def test_commit_changes_uses_git_add_all_when_paths_empty_list(monkeypatch):
    """commit_changes(paths=[]) must run 'git add -A', not 'git add -- '."""
    captured: list[tuple] = []

    def fake_git(*args, **kwargs):
        captured.append(args)
        if args[0] == "commit":
            return _cp()
        if args[0] == "rev-parse":
            return _cp(stdout="00000001\n")
        return _cp()

    monkeypatch.setattr("tools.git_ops._git", fake_git)
    from tools.git_ops import commit_changes

    commit_changes("msg", paths=[])
    add_calls = [c for c in captured if c[0] == "add"]
    assert len(add_calls) == 1
    assert add_calls[0] == ("add", "-A"), f"Expected git add -A for empty list, got {add_calls[0]}"
