"""Gate orchestrator.

evaluate_completion(workspace, tier) detects stacks, runs the tier's gate
commands per stack, records evidence, optionally checks git hygiene, and
returns a pass/fail GateResult with a human-readable summary used as the
block message on the pre_kanban_complete hook.

Pass rule: every NON-skipped run passed AND hygiene clean (when checked).
Skipped runs (missing toolchain / not allow-listed) do not fail the gate.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Union

from . import detect
from . import evidence
from . import githygiene
from . import registry
from . import runner
from . import tiers

logger = logging.getLogger(__name__)

# Default total wall-clock budget for one evaluate_completion call. Bounds a
# polyglot card so it cannot consume 600s x N stacks x M kinds.
DEFAULT_MAX_TOTAL_S = 1800.0


def _budget_skip(cmd: List[str], cwd: Union[str, Path]) -> "runner.GateRun":
    """A synthetic skipped run for a command not executed due to the budget."""
    return runner.GateRun(
        cmd=list(cmd), cwd=str(cwd), rc=-4, stdout="", stderr="",
        duration_s=0.0, passed=False, skipped=True,
        reason="total budget exceeded",
    )


@dataclass(frozen=True)
class GateResult:
    passed: bool
    summary: str
    runs: List[runner.GateRun] = field(default_factory=list)
    stacks: List[str] = field(default_factory=list)
    tier: str = tiers.DEFAULT_TIER
    hygiene_clean: bool = True


def _fmt_run(stack: str, kind: str, r: runner.GateRun) -> str:
    if r.skipped:
        return f"  SKIP {stack}/{kind}: {r.reason or 'skipped'} ({' '.join(r.cmd)})"
    state = "PASS" if r.passed else "FAIL"
    return f"  {state} {stack}/{kind}: rc={r.rc} ({' '.join(r.cmd)})"


def evaluate_completion(
    workspace: Union[str, Path],
    tier: str = tiers.DEFAULT_TIER,
    *,
    task_id: str = "",
    check_hygiene: bool = True,
    max_total_s: float = DEFAULT_MAX_TOTAL_S,
) -> GateResult:
    tier = tiers.normalise_tier(tier)
    stacks = detect.detect_stacks(workspace)
    if not stacks:
        return GateResult(
            passed=True, summary="quality-gate: no recognised stack; gate skipped.",
            runs=[], stacks=[], tier=tier, hygiene_clean=True,
        )

    runs: List[runner.GateRun] = []
    lines: List[str] = []
    kinds = tiers.kinds_for_tier(tier)
    any_real_failure = False

    start = time.monotonic()
    budget_blown = False
    for stack in stacks:
        gate_map = registry.gates_for(stack)
        for kind in kinds:
            for cmd in gate_map.get(kind, []):
                if time.monotonic() - start >= max_total_s:
                    # Budget exhausted: record the remaining commands as skips
                    # WITHOUT executing them, so a polyglot card cannot run away.
                    if not budget_blown:
                        logger.warning(
                            "quality-gate: total budget %.0fs exceeded; skipping "
                            "remaining gates for %s", max_total_s, task_id or workspace,
                        )
                        budget_blown = True
                    r = _budget_skip(cmd, workspace)
                else:
                    r = runner.run_gate(cmd, workspace)
                runs.append(r)
                evidence.record_run(
                    workspace, r, kind=kind, stack=stack, tier=tier, task_id=task_id,
                )
                lines.append(_fmt_run(stack, kind, r))
                if not r.skipped and not r.passed:
                    any_real_failure = True

    hygiene_clean = True
    if check_hygiene:
        hyg = githygiene.check_hygiene(workspace)
        hygiene_clean = hyg.clean
        if not hyg.clean:
            lines.append(
                "  FAIL git-hygiene: dirty working tree: "
                + ", ".join(hyg.dirty_paths[:10])
            )

    passed = (not any_real_failure) and hygiene_clean
    header = (
        f"quality-gate [{tier}] over {', '.join(stacks)}: "
        f"{'PASS' if passed else 'FAIL'}"
    )
    summary = header + "\n" + "\n".join(lines) if lines else header
    return GateResult(
        passed=passed, summary=summary, runs=runs, stacks=stacks,
        tier=tier, hygiene_clean=hygiene_clean,
    )
