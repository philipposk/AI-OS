"""Phase Z: CodeRabbit-style PR review."""
from __future__ import annotations

import json
import subprocess

import pytest


def _cp(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


# ---------- gh_pr_diff / gh_pr_post_review wrappers ----------


def test_gh_pr_diff_returns_text(monkeypatch):
    monkeypatch.setattr("tools.git_ops.shutil.which", lambda _: "/x/gh")
    monkeypatch.setattr("tools.git_ops._gh", lambda *a, **k: _cp(stdout="diff --git a/x b/x\n"))
    from tools.git_ops import gh_pr_diff

    assert gh_pr_diff(7) == "diff --git a/x b/x\n"


def test_gh_pr_diff_none_when_gh_missing(monkeypatch):
    monkeypatch.setattr("tools.git_ops.shutil.which", lambda _: None)
    from tools.git_ops import gh_pr_diff

    assert gh_pr_diff(1) is None


def test_gh_pr_post_review_passes_event_flag(monkeypatch):
    monkeypatch.setattr("tools.git_ops.shutil.which", lambda _: "/x/gh")
    seen = {}

    def fake_gh(*args, **k):
        seen["args"] = args
        return _cp()

    monkeypatch.setattr("tools.git_ops._gh", fake_gh)
    from tools.git_ops import gh_pr_post_review

    assert gh_pr_post_review(7, body="hi", event="APPROVE") is True
    assert "--approve" in seen["args"]
    assert "--body" in seen["args"]


def test_gh_pr_post_line_comment_uses_api(monkeypatch):
    monkeypatch.setattr("tools.git_ops.shutil.which", lambda _: "/x/gh")
    calls: list = []

    def fake_gh(*args, **k):
        calls.append(list(args))
        if args[:2] == ("pr", "view") and "headRefOid" in args:
            return _cp(stdout=json.dumps({"headRefOid": "abc123"}))
        if args[:2] == ("repo", "view"):
            return _cp(stdout=json.dumps({"nameWithOwner": "a/b"}))
        if args[0] == "api":
            return _cp(stdout="{}")
        return _cp(returncode=1)

    monkeypatch.setattr("tools.git_ops._gh", fake_gh)
    # gh_pr_view is also called and returns a different (non-headRefOid) shape;
    # patch via _gh_json so we don't have to also handle PR-view in fake_gh.
    monkeypatch.setattr("tools.git_ops.gh_pr_view", lambda *a, **k: {"number": 7})
    from tools.git_ops import gh_pr_post_line_comment

    ok = gh_pr_post_line_comment(7, body="boom", path="x.py", line=42)
    assert ok is True
    # Verify the api call shape.
    api_call = next(c for c in calls if c and c[0] == "api")
    assert "/repos/a/b/pulls/7/comments" in api_call
    assert "path=x.py" in api_call
    assert "line=42" in api_call
    assert "commit_id=abc123" in api_call


# ---------- pr_review pipeline ----------


def _make_chat_router(monkeypatch, summary_text: str, lines_text: str):
    """Patch router.ModelRouter.chat to return summary first, then line-comments."""
    sequence = iter([summary_text, lines_text])

    class _FakeResult:
        def __init__(self, text):
            self.text = text
            self.model = "fake"
            self.provider = "fake"
            self.prompt_tokens = 0
            self.completion_tokens = 0

    class _FakeRouter:
        def __init__(self, *a, **k):
            pass

        def chat(self, *a, **k):
            return _FakeResult(next(sequence))

    monkeypatch.setattr("router.ModelRouter", _FakeRouter)


def test_review_pr_dry_run(monkeypatch):
    monkeypatch.setattr("tools.pr_review.gh_available", lambda: True)
    monkeypatch.setattr("tools.pr_review.gh_pr_view", lambda n: {
        "number": 9, "title": "T", "state": "OPEN", "body": "B",
        "baseRefName": "main", "headRefName": "feat/x",
    })
    monkeypatch.setattr("tools.pr_review.gh_pr_diff", lambda n: "diff --git a/x b/x\n@@ -1 +1 @@\n-old\n+new\n")
    _make_chat_router(
        monkeypatch,
        summary_text="Looks fine. LGTM",
        lines_text=json.dumps([
            {"path": "x", "line": 1, "body": "rename `new` for clarity"},
            {"path": "x", "line": 1, "body": "second comment"},
        ]),
    )

    from tools.pr_review import review_pr

    result = review_pr(9, dry_run=True)
    assert result.pr_number == 9
    assert "LGTM" in result.summary
    assert len(result.line_comments) == 2
    assert result.line_comments[0].path == "x"
    assert result.line_comments[0].line == 1
    assert result.posted is False  # dry_run skipped posting
    assert result.posted_line_comments == 0


def test_review_pr_filters_invalid_line_comments(monkeypatch):
    monkeypatch.setattr("tools.pr_review.gh_available", lambda: True)
    monkeypatch.setattr("tools.pr_review.gh_pr_view", lambda n: {"number": 9, "title": "T", "state": "OPEN"})
    monkeypatch.setattr("tools.pr_review.gh_pr_diff", lambda n: "diff content")
    _make_chat_router(
        monkeypatch,
        summary_text="ok",
        lines_text=json.dumps([
            {"path": "x", "line": 1, "body": "valid"},
            {"path": "", "line": 2, "body": "empty path"},
            {"path": "y", "line": -1, "body": "negative line"},
            {"path": "z", "body": "missing line"},
            {"path": "ok", "line": "not int", "body": "string line"},
            "not an object",
        ]),
    )

    from tools.pr_review import review_pr

    result = review_pr(9, dry_run=True)
    assert len(result.line_comments) == 1
    assert result.line_comments[0].path == "x"


def test_review_pr_posts_when_not_dry_run(monkeypatch):
    monkeypatch.setattr("tools.pr_review.gh_available", lambda: True)
    monkeypatch.setattr("tools.pr_review.gh_pr_view", lambda n: {"number": 9, "title": "T", "state": "OPEN"})
    monkeypatch.setattr("tools.pr_review.gh_pr_diff", lambda n: "diff content")
    posted = {"summary": False, "lines": 0}

    def fake_summary(num, *, body, event="COMMENT"):
        posted["summary"] = True
        posted["event"] = event
        return True

    def fake_line(num, *, body, path, line, commit_sha=None):
        posted["lines"] += 1
        return True

    monkeypatch.setattr("tools.pr_review.gh_pr_post_review", fake_summary)
    monkeypatch.setattr("tools.pr_review.gh_pr_post_line_comment", fake_line)
    _make_chat_router(
        monkeypatch,
        summary_text="bad design CHANGES_REQUESTED",
        lines_text=json.dumps([
            {"path": "x", "line": 1, "body": "fix"},
            {"path": "y", "line": 2, "body": "fix"},
        ]),
    )

    from tools.pr_review import review_pr

    result = review_pr(9)
    assert result.posted is True
    assert posted["event"] == "REQUEST_CHANGES"
    assert posted["lines"] == 2
    assert result.posted_line_comments == 2


def test_review_pr_raises_without_gh(monkeypatch):
    monkeypatch.setattr("tools.pr_review.gh_available", lambda: False)
    from tools.pr_review import review_pr

    with pytest.raises(RuntimeError, match="gh"):
        review_pr(9)


def test_review_pr_raises_when_diff_empty(monkeypatch):
    monkeypatch.setattr("tools.pr_review.gh_available", lambda: True)
    monkeypatch.setattr("tools.pr_review.gh_pr_view", lambda n: {"number": 9})
    monkeypatch.setattr("tools.pr_review.gh_pr_diff", lambda n: "")
    from tools.pr_review import review_pr

    with pytest.raises(RuntimeError, match="empty diff"):
        review_pr(9)


def test_review_pr_trims_long_diff(monkeypatch):
    """Large diffs are clipped to PR_REVIEW_MAX_DIFF_CHARS before being sent."""
    monkeypatch.setenv("PR_REVIEW_MAX_DIFF_CHARS", "200")
    monkeypatch.setattr("tools.pr_review.gh_available", lambda: True)
    monkeypatch.setattr("tools.pr_review.gh_pr_view", lambda n: {"number": 9})
    huge = "x" * 5000
    monkeypatch.setattr("tools.pr_review.gh_pr_diff", lambda n: huge)
    captured = {}

    class _FakeResult:
        def __init__(self, text):
            self.text = text
            self.model = self.provider = "f"
            self.prompt_tokens = self.completion_tokens = 0

    class _FakeRouter:
        def chat(self, msgs, *a, **kw):
            captured.setdefault("user", msgs[1]["content"])
            return _FakeResult("ok" if "user" not in captured or len(captured) == 1 else "[]")

    monkeypatch.setattr("router.ModelRouter", lambda *a, **k: _FakeRouter())

    from tools.pr_review import review_pr

    review_pr(9, dry_run=True)
    assert "[diff truncated" in captured["user"]
