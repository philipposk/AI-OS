"""Tiny safe wrappers around git. Used by the orchestrator commit node and
the Phase F dashboard for diff previews.

Phase R: adds `gh` CLI wrappers (`gh_pr_view` etc.) plus `git_log` /
`git_revert` so the orchestrator can pull issue/PR context and the
dashboard can show + revert recent commits.
"""
from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable, List, Optional


def _git(*args: str, cwd: str | Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd) if cwd else None, capture_output=True, text=True, check=check)


def _gh(*args: str, cwd: str | Path | None = None) -> subprocess.CompletedProcess:
    """Shell out to the GitHub CLI. Always non-checked; caller decides on failure."""
    return subprocess.run(["gh", *args], cwd=str(cwd) if cwd else None, capture_output=True, text=True, check=False)


def gh_available() -> bool:
    """True if the `gh` binary is on PATH. Cheap precondition for the wrappers."""
    return shutil.which("gh") is not None


def status_porcelain(cwd: str | Path | None = None) -> List[str]:
    res = _git("status", "--porcelain", cwd=cwd, check=False)
    return [line for line in res.stdout.splitlines() if line]


def diff(paths: Optional[Iterable[str]] = None, cwd: str | Path | None = None, staged: bool = False) -> str:
    args = ["diff"]
    if staged:
        args.append("--staged")
    if paths:
        args.append("--")
        args.extend(paths)
    return _git(*args, cwd=cwd, check=False).stdout


def commit_changes(
    message: str,
    paths: Optional[Iterable[str]] = None,
    cwd: str | Path | None = None,
) -> str:
    """Stage `paths` (or all changes if None) and create one commit. Returns SHA."""
    if paths:
        _git("add", "--", *paths, cwd=cwd)
    else:
        _git("add", "-A", cwd=cwd)
    res = _git("commit", "-m", message, cwd=cwd, check=False)
    if res.returncode != 0:
        raise RuntimeError(f"git commit failed: {res.stderr.strip() or res.stdout.strip()}")
    sha = _git("rev-parse", "HEAD", cwd=cwd).stdout.strip()
    return sha


# ---------- Phase R: git log + revert ----------


def git_log(
    paths: Optional[Iterable[str]] = None,
    limit: int = 20,
    cwd: str | Path | None = None,
) -> List[dict]:
    """Return recent commits as a list of dicts with sha/short/subject/author/date.

    If `paths` is given, only commits touching those paths are returned.
    """
    fmt = "%H%x1f%h%x1f%s%x1f%an%x1f%ad"
    args = ["log", f"--pretty=format:{fmt}", "--date=iso-strict", f"-n{int(limit)}"]
    if paths:
        args.append("--")
        args.extend(paths)
    res = _git(*args, cwd=cwd, check=False)
    out: List[dict] = []
    for line in res.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 5:
            continue
        sha, short, subject, author, date = parts
        out.append({"sha": sha, "short": short, "subject": subject, "author": author, "date": date})
    return out


def git_revert(sha: str, cwd: str | Path | None = None) -> str:
    """`git revert --no-edit <sha>`. Returns the new commit SHA. Raises on failure."""
    res = _git("revert", "--no-edit", sha, cwd=cwd, check=False)
    if res.returncode != 0:
        raise RuntimeError(f"git revert failed: {res.stderr.strip() or res.stdout.strip()}")
    return _git("rev-parse", "HEAD", cwd=cwd).stdout.strip()


# ---------- Phase R: gh PR/issue context ----------

_PR_FIELDS = "number,title,state,author,body,headRefName,baseRefName,url,labels"
_ISSUE_FIELDS = "number,title,state,author,body,url,labels"


def _gh_json(args: List[str], cwd: str | Path | None = None) -> Optional[Any]:
    if not gh_available():
        return None
    res = _gh(*args, cwd=cwd)
    if res.returncode != 0:
        return None
    try:
        return json.loads(res.stdout) if res.stdout.strip() else None
    except json.JSONDecodeError:
        return None


def gh_pr_view(num: int | str, cwd: str | Path | None = None) -> Optional[dict]:
    return _gh_json(["pr", "view", str(num), "--json", _PR_FIELDS], cwd=cwd)


def gh_pr_list(state: str = "open", limit: int = 20, cwd: str | Path | None = None) -> List[dict]:
    out = _gh_json(["pr", "list", "--state", state, "--limit", str(limit), "--json", _PR_FIELDS], cwd=cwd)
    return out if isinstance(out, list) else []


def gh_issue_view(num: int | str, cwd: str | Path | None = None) -> Optional[dict]:
    return _gh_json(["issue", "view", str(num), "--json", _ISSUE_FIELDS], cwd=cwd)


def gh_issue_list(state: str = "open", limit: int = 20, cwd: str | Path | None = None) -> List[dict]:
    out = _gh_json(["issue", "list", "--state", state, "--limit", str(limit), "--json", _ISSUE_FIELDS], cwd=cwd)
    return out if isinstance(out, list) else []


def gh_repo_default_branch(cwd: str | Path | None = None) -> Optional[str]:
    out = _gh_json(["repo", "view", "--json", "defaultBranchRef"], cwd=cwd)
    if not isinstance(out, dict):
        return None
    ref = out.get("defaultBranchRef")
    return ref.get("name") if isinstance(ref, dict) else None


def gh_pr_diff(num: int | str, cwd: str | Path | None = None) -> Optional[str]:
    """Raw unified diff for the PR. None if gh missing / request fails."""
    if not gh_available():
        return None
    res = _gh("pr", "diff", str(num), cwd=cwd)
    if res.returncode != 0:
        return None
    return res.stdout


def gh_pr_post_review(num: int | str, *, body: str, event: str = "COMMENT",
                      cwd: str | Path | None = None) -> bool:
    """Post a top-level review (summary) on a PR.

    event ∈ {"COMMENT", "APPROVE", "REQUEST_CHANGES"}.
    Returns True on success.
    """
    if not gh_available():
        return False
    flag = {"COMMENT": "--comment", "APPROVE": "--approve",
            "REQUEST_CHANGES": "--request-changes"}.get(event, "--comment")
    res = _gh("pr", "review", str(num), flag, "--body", body, cwd=cwd)
    return res.returncode == 0


def gh_pr_post_line_comment(num: int | str, *, body: str, path: str, line: int,
                            commit_sha: Optional[str] = None,
                            cwd: str | Path | None = None) -> bool:
    """Post a single line-comment on a PR via `gh api`.

    GitHub's PR-review API requires the commit SHA of the head ref. We look
    it up from gh_pr_view() when the caller doesn't pass one.
    """
    if not gh_available():
        return False
    if not commit_sha:
        pr = gh_pr_view(num, cwd=cwd) or {}
        # gh's "headRefOid" isn't in our default field set; ask for it.
        extra = _gh_json(["pr", "view", str(num), "--json", "headRefOid"], cwd=cwd)
        if isinstance(extra, dict):
            commit_sha = extra.get("headRefOid")
        if not commit_sha:
            return False
    # Repo owner/name needed for /repos/:owner/:repo/pulls/:num/comments.
    repo_meta = _gh_json(["repo", "view", "--json", "nameWithOwner"], cwd=cwd)
    if not isinstance(repo_meta, dict):
        return False
    nwo = repo_meta.get("nameWithOwner")
    if not nwo:
        return False
    res = _gh(
        "api", "-X", "POST",
        f"/repos/{nwo}/pulls/{num}/comments",
        "-f", f"body={body}",
        "-f", f"commit_id={commit_sha}",
        "-f", f"path={path}",
        "-F", f"line={int(line)}",
        "-f", "side=RIGHT",
        cwd=cwd,
    )
    return res.returncode == 0


# ---------- Phase R: task-text → PR/issue numbers ----------

import re as _re

_PR_URL_RE = _re.compile(r"github\.com/[^/\s]+/[^/\s]+/(?:pull|issues)/(\d+)")
_HASH_NUM_RE = _re.compile(r"(?<![\w/])#(\d+)\b")


def parse_issue_refs(text: str) -> List[int]:
    """Find issue/PR numbers in arbitrary text. Returns de-duplicated, in order.

    Matches:
      - github.com/<owner>/<repo>/pull/123 or /issues/123
      - bare `#123` (word-boundary, not in middle of a path)
    """
    nums: List[int] = []
    seen: set[int] = set()
    for m in _PR_URL_RE.finditer(text):
        n = int(m.group(1))
        if n not in seen:
            nums.append(n)
            seen.add(n)
    for m in _HASH_NUM_RE.finditer(text):
        n = int(m.group(1))
        if n not in seen:
            nums.append(n)
            seen.add(n)
    return nums


def gh_context_for_task(text: str, cwd: str | Path | None = None, max_refs: int = 3) -> str:
    """Resolve issue/PR refs in `text` to formatted context blocks.

    Returns "" if `gh` is missing, no refs are found, or all lookups fail.
    Tries PR first (covers PR URLs); falls back to issue.
    """
    if not gh_available():
        return ""
    refs = parse_issue_refs(text)
    if not refs:
        return ""
    blocks: List[str] = []
    for n in refs[:max_refs]:
        item = gh_pr_view(n, cwd=cwd) or gh_issue_view(n, cwd=cwd)
        if not item:
            continue
        kind = "PR" if "headRefName" in item else "Issue"
        labels = ",".join(l.get("name", "") for l in (item.get("labels") or []) if isinstance(l, dict))
        body = (item.get("body") or "").strip()
        blocks.append(
            f"[{kind} #{item.get('number')}] {item.get('title')} ({item.get('state')})\n"
            f"  url:    {item.get('url')}\n"
            f"  labels: {labels}\n"
            f"  body:\n{body[:1200]}"
        )
    return "\n\n".join(blocks)
