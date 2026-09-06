"""Git working-tree hygiene check.

Reports whether *workspace* is a clean git tree. A non-repo is reported clean
(there is nothing to be dirty about, so hygiene does not block it). The gate's
own evidence dir is git-ignored (see evidence.py) so it never shows as dirty.
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Union

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HygieneResult:
    is_repo: bool
    clean: bool
    dirty_paths: List[str] = field(default_factory=list)
    reason: str = ""


def check_hygiene(workspace: Union[str, Path]) -> HygieneResult:
    ws = str(workspace)
    try:
        proc = subprocess.run(
            ["git", "-C", ws, "status", "--porcelain"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("quality-gate: git hygiene check could not run: %s", exc)
        return HygieneResult(is_repo=False, clean=True, reason="git unavailable")

    if proc.returncode != 0:
        # Not a git repo (or git errored) -> nothing to be dirty about.
        return HygieneResult(is_repo=False, clean=True, reason="not a git repo")

    dirty: List[str] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        # porcelain: 2 status chars + space + path
        dirty.append(line[3:] if len(line) > 3 else line.strip())
    return HygieneResult(is_repo=True, clean=(not dirty), dirty_paths=dirty)
