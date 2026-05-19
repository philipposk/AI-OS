"""CodeRabbit-style automated PR review with severity + optional crew mode.

Pipeline:
    1. Fetch PR metadata + unified diff via `gh`.
    2. EITHER:
       - Single-LLM (default): one Reviewer call (summary) + one Critic call
         (line comments).
       - Crew (`CREW_MODE=true` or `crew=True` arg): Critic + Tester + Reviewer
         each get the diff; their notes are merged into the summary and the
         union of their line-comments is deduplicated.
    3. Each line comment carries severity ∈ {info, minor, major, security}.
       The posted body is prefixed with a `[major]` / `[security]` tag so
       GitHub readers can triage at a glance.
    4. Inline fix suggestions: when a line comment includes a short
       replacement snippet, append a GitHub `suggestion` code block so
       reviewers can click "Apply suggestion".
    5. Post the summary as a top-level review with verdict (APPROVE /
       COMMENT / REQUEST_CHANGES) and each line-comment via `gh api` against
       the PR head SHA. Failures per-comment are logged but never abort.

Diffs are truncated to PR_REVIEW_MAX_DIFF_CHARS (default 60k) to keep token
use bounded.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from typing import List, Optional

from tools.git_ops import (
    gh_available,
    gh_pr_diff,
    gh_pr_post_line_comment,
    gh_pr_post_review,
    gh_pr_view,
)

logger = logging.getLogger(__name__)

SEVERITIES = ("security", "major", "minor", "info")
_SEV_SET = set(SEVERITIES)


@dataclass
class LineComment:
    path: str
    line: int
    body: str
    severity: str = "minor"        # security | major | minor | info
    suggestion: Optional[str] = None  # short single-block replacement (markdown suggestion)


@dataclass
class ReviewResult:
    pr_number: int
    summary: str
    verdict: str = "COMMENT"        # APPROVE | COMMENT | REQUEST_CHANGES
    line_comments: List[LineComment] = field(default_factory=list)
    severity_counts: dict = field(default_factory=dict)
    posted: bool = False
    posted_line_comments: int = 0
    skipped_line_comments: int = 0
    mode: str = "single"            # "single" | "crew"


_SUMMARY_SYSTEM = (
    "You are a senior engineer reviewing a pull request. In <=200 words, "
    "summarise the change: intent, files touched, the most important risk "
    "or unresolved question. End with one of three verdicts in CAPS: "
    "LGTM / MINOR / CHANGES_REQUESTED. Be specific. No emoji, no marketing."
)

_LINES_SYSTEM = (
    "You are a code reviewer. Read the diff and produce STRICT JSON only — "
    "an array of line-comment objects. Each object has keys:\n"
    "  - `path`     relative path as it appears in the diff\n"
    "  - `line`     integer line number in the NEW file (must correspond "
    "to a `+` or context line shown in the hunk)\n"
    "  - `body`     1-3 sentences explaining a concrete concern (bug, "
    "security risk, perf hazard, missing edge case, naming/typo)\n"
    "  - `severity` one of: security, major, minor, info\n"
    "  - `suggestion` OPTIONAL single-line code replacement that would "
    "fix the issue — verbatim source for that line, no diff markers. Omit "
    "if a single-line fix isn't sensible.\n"
    "Limit to the 10 most important comments. Skip nitpicks (formatting). "
    "Reply ONLY with the JSON array — no markdown, no commentary."
)

_TESTER_SYSTEM = (
    "You are a Tester reviewing a pull request. Identify test-coverage gaps "
    "this diff creates: missing edge cases, error paths not exercised, race "
    "conditions, untested integration boundaries. Output STRICT JSON only — "
    "same array shape as the code reviewer (path, line, body, severity, "
    "optional suggestion). Severity bias toward `major` for missing critical "
    "paths. Limit 6 entries. No commentary."
)

_CRITIC_SYSTEM = (
    "You are a Critic reviewing a pull request. Focus on architecture: "
    "abstraction leaks, premature optimisation, scope creep, hard-coded "
    "values that should be config, missing observability, dead branches. "
    "Output STRICT JSON only — same array shape as the code reviewer. "
    "Limit 6 entries. No commentary."
)


def _trim_diff(diff: str) -> str:
    limit = int(os.getenv("PR_REVIEW_MAX_DIFF_CHARS", "60000"))
    if len(diff) <= limit:
        return diff
    half = limit // 2
    return diff[:half] + f"\n\n... [diff truncated, {len(diff) - limit} chars omitted] ...\n\n" + diff[-half:]


_JSON_ARR_RE = re.compile(r"\[.*\]", re.DOTALL)


def _parse_line_comments(text: str, *, default_severity: str = "minor") -> List[LineComment]:
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
        sev = str(item.get("severity") or default_severity).strip().lower()
        if sev not in _SEV_SET:
            sev = default_severity
        sug = item.get("suggestion")
        sug_s: Optional[str] = None
        if isinstance(sug, str) and sug.strip():
            # Single-line only; reject anything that's clearly a diff block.
            if "\n" not in sug and not sug.lstrip().startswith(("+", "-", "@@")):
                sug_s = sug.rstrip()
        out.append(LineComment(path=path, line=line, body=body, severity=sev, suggestion=sug_s))
    return out


def _dedup_line_comments(items: List[LineComment]) -> List[LineComment]:
    """Collapse exact (path, line) duplicates, keeping the highest severity."""
    sev_rank = {s: i for i, s in enumerate(SEVERITIES)}  # security=0 (highest)
    by_key: dict[tuple[str, int], LineComment] = {}
    for lc in items:
        key = (lc.path, lc.line)
        prev = by_key.get(key)
        if prev is None:
            by_key[key] = lc
            continue
        # Keep the higher-severity one (lower rank number). Combine bodies if both meaningful.
        if sev_rank.get(lc.severity, 9) < sev_rank.get(prev.severity, 9):
            winner = LineComment(path=lc.path, line=lc.line,
                                 body=f"{lc.body}\n\nAlso: {prev.body}",
                                 severity=lc.severity,
                                 suggestion=lc.suggestion or prev.suggestion)
            by_key[key] = winner
        else:
            prev.body = f"{prev.body}\n\nAlso: {lc.body}"
            if prev.suggestion is None and lc.suggestion:
                prev.suggestion = lc.suggestion
    # Stable order: severity → path → line
    return sorted(by_key.values(), key=lambda c: (sev_rank.get(c.severity, 9), c.path, c.line))


def _verdict(summary: str, line_comments: List[LineComment]) -> str:
    """REQUEST_CHANGES wins if a security/major issue exists OR the model said so."""
    if "CHANGES_REQUESTED" in summary:
        return "REQUEST_CHANGES"
    if any(lc.severity in ("security", "major") for lc in line_comments):
        return "REQUEST_CHANGES"
    if "LGTM" in summary:
        return "APPROVE"
    return "COMMENT"


def _format_body(lc: LineComment) -> str:
    """Severity tag + body + optional GitHub suggestion code block."""
    tag = f"[{lc.severity}]" if lc.severity != "minor" else ""
    parts = [f"{tag} {lc.body}".strip()]
    if lc.suggestion:
        parts.append(f"\n```suggestion\n{lc.suggestion}\n```")
    return "\n".join(parts)


def _severity_counts(items: List[LineComment]) -> dict:
    out = {s: 0 for s in SEVERITIES}
    for lc in items:
        out[lc.severity] = out.get(lc.severity, 0) + 1
    return out


def _crew_mode_on(crew_arg: Optional[bool]) -> bool:
    if crew_arg is not None:
        return bool(crew_arg)
    return os.getenv("CREW_MODE", "").strip().lower() in ("1", "true", "yes", "on")


def review_pr(num: int | str, *, dry_run: bool = False,
              crew: Optional[bool] = None,
              workflow_id: Optional[str] = None) -> ReviewResult:
    """Run the review pipeline. Always returns a ReviewResult, even on partial failure.

    Mode auto-picks from `crew` arg (state-style override) or $CREW_MODE env.
    """
    if not gh_available():
        raise RuntimeError("`gh` binary not on PATH — install GitHub CLI to use PR review")

    pr = gh_pr_view(num)
    if not pr:
        raise RuntimeError(f"PR #{num} not found or `gh` not authenticated")

    diff = gh_pr_diff(num) or ""
    if not diff.strip():
        raise RuntimeError(f"empty diff for PR #{num} — nothing to review")
    diff = _trim_diff(diff)

    from router import ModelRouter
    router = ModelRouter()

    user_block = (
        f"PR #{pr.get('number')} — {pr.get('title')}\n"
        f"State: {pr.get('state')}  base={pr.get('baseRefName')}  head={pr.get('headRefName')}\n"
        f"Body:\n{(pr.get('body') or '').strip()[:2000]}\n\n"
        f"Diff (truncated to {len(diff)} chars):\n```diff\n{diff}\n```"
    )

    crew_on = _crew_mode_on(crew)
    mode = "crew" if crew_on else "single"

    # ----- summary -----
    summary_msgs = [
        {"role": "system", "content": _SUMMARY_SYSTEM},
        {"role": "user", "content": user_block},
    ]
    res_summary = router.chat(summary_msgs, task_type="review", max_tokens=512,
                              temperature=0.2, workflow_id=workflow_id)
    summary = res_summary.text.strip()

    # ----- line comments (single vs crew) -----
    line_comments: List[LineComment] = []

    def _ask(system: str, default_sev: str, max_tokens: int = 1024) -> List[LineComment]:
        r = router.chat(
            [{"role": "system", "content": system},
             {"role": "user", "content": user_block}],
            task_type="review", max_tokens=max_tokens, temperature=0.2,
            workflow_id=workflow_id,
        )
        return _parse_line_comments(r.text, default_severity=default_sev)

    if crew_on:
        line_comments.extend(_ask(_LINES_SYSTEM, "minor", max_tokens=1024))
        line_comments.extend(_ask(_TESTER_SYSTEM, "major", max_tokens=768))
        line_comments.extend(_ask(_CRITIC_SYSTEM, "minor", max_tokens=768))
        line_comments = _dedup_line_comments(line_comments)
    else:
        line_comments = _ask(_LINES_SYSTEM, "minor", max_tokens=1024)
        line_comments = _dedup_line_comments(line_comments)

    # Cap output volume to keep PR noise bounded.
    line_comments = line_comments[: int(os.getenv("PR_REVIEW_MAX_COMMENTS", "12"))]

    sev_counts = _severity_counts(line_comments)
    verdict = _verdict(summary, line_comments)

    result = ReviewResult(
        pr_number=int(pr.get("number") or num),
        summary=summary,
        verdict=verdict,
        line_comments=line_comments,
        severity_counts=sev_counts,
        mode=mode,
    )

    if dry_run:
        return result

    sev_summary = " · ".join(f"{c} {s}" for s, c in sev_counts.items() if c) or "no findings"
    body = f"{summary}\n\n— ai_company review ({mode} mode) · {sev_summary}"
    result.posted = gh_pr_post_review(num, body=body, event=verdict)

    for lc in line_comments:
        ok = gh_pr_post_line_comment(num, body=_format_body(lc), path=lc.path, line=lc.line)
        if ok:
            result.posted_line_comments += 1
        else:
            result.skipped_line_comments += 1
            logger.warning("line-comment skipped: %s:%d (body=%r)", lc.path, lc.line, lc.body[:80])

    return result


def result_to_dict(r: ReviewResult) -> dict:
    """Helper for cli / api JSON output."""
    return {
        "pr_number": r.pr_number,
        "mode": r.mode,
        "verdict": r.verdict,
        "summary": r.summary,
        "severity_counts": r.severity_counts,
        "line_comments": [asdict(lc) for lc in r.line_comments],
        "posted": r.posted,
        "posted_line_comments": r.posted_line_comments,
        "skipped_line_comments": r.skipped_line_comments,
    }
