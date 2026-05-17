"""Tiny safe wrappers around git. Used by the orchestrator commit node and
the Phase F dashboard for diff previews.
"""
from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from typing import Iterable, List, Optional


def _git(*args: str, cwd: str | Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd) if cwd else None, capture_output=True, text=True, check=check)


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
