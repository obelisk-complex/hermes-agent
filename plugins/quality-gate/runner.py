"""Subprocess gate runner.

Runs a single allow-listed command with shell=False, captures rc/stdout/stderr
decoded as utf-8 with errors="replace" (gate tools emit arbitrary bytes), and
enforces a timeout. Classifies the result:

  * not allow-listed  -> skipped (rc -1), never executed
  * executable missing -> skipped (rc -2), not a fail (CI may lack a toolchain)
  * timeout           -> fail (rc -3)
  * rc 0              -> pass
  * rc 5 + pytest     -> pass ("no tests collected" is not a failure)
  * any other rc      -> fail
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Union

from . import registry

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 600


@dataclass(frozen=True)
class GateRun:
    cmd: List[str]
    cwd: str
    rc: int
    stdout: str
    stderr: str
    duration_s: float
    passed: bool
    skipped: bool
    reason: str = ""


def _classify(cmd: List[str], rc: int) -> bool:
    """Return True if *rc* counts as a pass for *cmd*."""
    if rc == 0:
        return True
    if rc == 5 and os.path.basename(cmd[0]) == "pytest":
        # pytest exit 5 = "no tests collected" — not a failure.
        return True
    return False


def run_gate(
    cmd: List[str],
    cwd: Union[str, Path],
    *,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> GateRun:
    cwd_str = str(cwd)
    if not registry.is_allowed(cmd):
        logger.warning("quality-gate: refusing non-allow-listed command %r", cmd)
        return GateRun(cmd, cwd_str, -1, "", "", 0.0, False, True, "not allow-listed")

    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd_str,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
    except (FileNotFoundError, PermissionError):
        # PermissionError covers WSL paths that resolve to a Windows executable
        # which cannot be invoked from Linux — treat as "not available", same as
        # a missing binary.  Both conditions mean the toolchain is absent here.
        logger.warning("quality-gate: executable not found/accessible for %r (skipping)", cmd)
        return GateRun(
            cmd, cwd_str, -2, "", "", time.monotonic() - start,
            False, True, "executable not found",
        )
    except subprocess.TimeoutExpired as exc:
        logger.warning("quality-gate: command timed out after %ss: %r", timeout_s, cmd)
        out = exc.stdout or ""
        err = exc.stderr or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
        return GateRun(
            cmd, cwd_str, -3, out, err, time.monotonic() - start,
            False, False, "timeout",
        )

    duration = time.monotonic() - start
    passed = _classify(cmd, proc.returncode)
    return GateRun(
        cmd, cwd_str, proc.returncode, proc.stdout, proc.stderr,
        duration, passed, False, "",
    )
