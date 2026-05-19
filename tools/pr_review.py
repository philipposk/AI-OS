"""CodeRabbit-style automated PR review.

Pipeline:
    1. Fetch PR metadata + unified diff via `gh`.
    2. Run our Reviewer + Critic crew over the diff (Reviewer writes a
       short narrative summary; Critic produces a JSON array of
       line-comments).
    3. Post the summary as a top-level review and each line-comment via
       `gh api` against the PR head SHA. Failures per-comment are logged
       but never abort the run.

Single entry point: `review_pr(num, dry_run=False)`. `dry_run=True` returns
the would-be payload without posting, useful for testing or piping into
the dashboard.

Cost: one Reviewer call + one Critic call per PR. Both task_type="review",
so the cheap-or-free model in the router is picked by default. Diffs are
truncated to PR_REVIEW_MAX_DIFF_CHARS (default 60k) to keep token use
bounded.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import List, Optional

from tools.git_ops import (
    gh_available,
    gh_pr_diff,
    gh_pr_post_line_comment,
    gh_pr_post_review,
    gh_pr_view,
)

logger = logging.getLogger(__name__)


@dataclass
class LineComment:
    path: str
    line: int
    body: str


@dataclass
class ReviewResult:
    pr_number: int
    summary: str
    line_comments: List[LineComment] = field(default_factory=list)
    posted: bool = False                  # True when at least the summary made it
    posted_line_comments: int = 0
    skipped_line_comments: int = 0


_SUMMARY_SYSTEM = (
    "You are a senior engineer reviewing a pull request. In <=200 words, "
    "summarise the change: intent, files touched, the most important risk "
    "or unresolved question. End with one of three verdicts in CAPS: "
    "LGTM / MINOR / CHANGES_REQUESTED. Be specific. No emoji, no marketing."
)

_LINES_SYSTEM = (
    "You are a code reviewer. Read the diff below and produce STRICT JSON "
    "only — an array of line-comment objects. Each object has keys: "
    '`path` (relative path as it appears in the diff), `line` (integer '
    "line number in the NEW file, must correspond to a `+` or context "
    "line shown in the hunk), and `body` (1-3 sentences explaining a "
    "concrete concern: bug, security risk, performance hazard, missing "
    "edge case, or a name/typo problem). Limit to the 10 most important "
    "comments. Skip nitpicks (formatting, style preferences). Reply ONLY "
    "with the JSON array — no markdown, no commentary."
)


def _trim_diff(diff: str) -> str:
    limit = int(os.getenv("PR_REVIEW_MAX_DIFF_CHARS", "60000"))
    if len(diff) <= limit:
        return diff
    # Keep the head + tail so the model sees both the first and last files.
    half = limit // 2
    return diff[:half] + f"\n\n... [diff truncated, {len(diff) - limit} chars omitted] ...\n\n" + diff[-half:]


_JSON_ARR_RE = re.compile(r"\[.*\]", re.DOTALL)


def _parse_line_comments(text: str) -> List[LineComment]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    m = _JSON_ARR_RE.search(text)
    raw = m.group(0) if m else text
    try:
        arr = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning("PR-review line-comment JSON parse failed: %s", e)
        return []
    out: List[LineComment] = []
    if not isinstance(arr, list):
        return out
    for item in arr:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        body = str(item.get("body") or "").strip()
        try:
            line = int(item.get("line"))
        except (TypeError, ValueError):
            continue
        if not path or not body or line <= 0:
            continue
        out.append(LineComment(path=path, line=line, body=body))
    return out[:10]


def _verdict(summary: str) -> str:
    if "CHANGES_REQUESTED" in summary:
        return "REQUEST_CHANGES"
    if "LGTM" in summary:
        return "APPROVE"
    return "COMMENT"


def review_pr(num: int | str, *, dry_run: bool = False,
              workflow_id: Optional[str] = None) -> ReviewResult:
    """Run the review pipeline. Always returns a ReviewResult, even on partial failure."""
    if not gh_available():
        raise RuntimeError("`gh` binary not on PATH — install GitHub CLI to use PR review")

    pr = gh_pr_view(num)
    if not pr:
        raise RuntimeError(f"PR #{num} not found or `gh` not authenticated")

    diff = gh_pr_diff(num) or ""
    if not diff.strip():
        raise RuntimeError(f"empty diff for PR #{num} — nothing to review")
    diff = _trim_diff(diff)

    # Lazy router import keeps tools/ standalone for unit tests.
    from router import ModelRouter
    router = ModelRouter()

    user_summary = (
        f"PR #{pr.get('number')} — {pr.get('title')}\n"
        f"State: {pr.get('state')}  base={pr.get('baseRefName')}  head={pr.get('headRefName')}\n"
        f"Body:\n{(pr.get('body') or '').strip()[:2000]}\n\n"
        f"Diff (truncated to {len(diff)} chars):\n```diff\n{diff}\n```"
    )

    res_summary = router.chat(
        [{"role": "system", "content": _SUMMARY_SYSTEM},
         {"role": "user", "content": user_summary}],
        task_type="review", max_tokens=512, temperature=0.2,
        workflow_id=workflow_id,
    )
    summary = res_summary.text.strip()

    res_lines = router.chat(
        [{"role": "system", "content": _LINES_SYSTEM},
         {"role": "user", "content": user_summary}],
        task_type="review", max_tokens=1024, temperature=0.2,
        workflow_id=workflow_id,
    )
    line_comments = _parse_line_comments(res_lines.text)

    result = ReviewResult(pr_number=int(pr.get("number") or num),
                          summary=summary, line_comments=line_comments)

    if dry_run:
        return result

    verdict = _verdict(summary)
    posted = gh_pr_post_review(num, body=summary, event=verdict)
    result.posted = posted

    for lc in line_comments:
        ok = gh_pr_post_line_comment(num, body=lc.body, path=lc.path, line=lc.line)
        if ok:
            result.posted_line_comments += 1
        else:
            result.skipped_line_comments += 1
            logger.warning("line-comment skipped: %s:%d (body=%r)", lc.path, lc.line, lc.body[:80])

    return result
