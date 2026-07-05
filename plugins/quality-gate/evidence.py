"""Record each gate run under <workspace>/.hermes/gate-runs/.

Critically, the evidence dir writes its OWN .gitignore ("*") so the recorded
JSON never appears in ``git status`` and the gate cannot dirty the tree it is
meant to be checking.
"""
from __future__ import annotations

import json
import logging
import re
import secrets
import time
from pathlib import Path
from typing import Union

from . import runner

logger = logging.getLogger(__name__)

_MAX_OUTPUT = 8000

# Filename-safe slug: keep alnum/dash/underscore, collapse everything else.
_SAFE = re.compile(r"[^A-Za-z0-9_-]+")


def _slug(value: str) -> str:
    return _SAFE.sub("-", value or "").strip("-")


def evidence_dir(workspace: Union[str, Path]) -> Path:
    """Return <workspace>/.hermes/gate-runs, ensuring it + its .gitignore."""
    d = Path(workspace) / ".hermes" / "gate-runs"
    d.mkdir(parents=True, exist_ok=True)
    gi = d / ".gitignore"
    if not gi.exists():
        # Ignore everything in here so evidence never dirties the working tree.
        gi.write_text("*\n", encoding="utf-8")
    return d


def record_run(
    workspace: Union[str, Path],
    gate_run: runner.GateRun,
    *,
    kind: str,
    stack: str,
    tier: str,
    task_id: str = "",
) -> Path:
    d = evidence_dir(workspace)
    ts = time.strftime("%Y%m%dT%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
    # task_id[:8] + a short random hex suffix guarantee distinct filenames even
    # for two runs of the same stack/kind in the same millisecond (retry races).
    task8 = _slug(task_id)[:8] or "noid"
    rand = secrets.token_hex(3)
    path = d / f"{ts}-{task8}-{_slug(stack)}-{_slug(kind)}-{rand}.json"
    payload = {
        "ts": ts,
        "task_id": task_id,
        "stack": stack,
        "kind": kind,
        "tier": tier,
        "cmd": list(gate_run.cmd),
        "rc": gate_run.rc,
        "passed": gate_run.passed,
        "skipped": gate_run.skipped,
        "reason": gate_run.reason,
        "duration_s": round(gate_run.duration_s, 3),
        "stdout": gate_run.stdout[:_MAX_OUTPUT],
        "stderr": gate_run.stderr[:_MAX_OUTPUT],
    }
    try:
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("quality-gate: failed to record evidence %s: %s", path, exc)
    return path
