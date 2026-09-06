"""on_pre_kanban_complete: run the mechanical gate; block completion on failure.

Fired by the fork's BLOCK-CAPABLE ``pre_kanban_complete`` hook. Returning
{"action":"block","message": summary} stops the card from completing; None
allows it. Fail-closed: if the gate itself errors, we BLOCK (a card must not
slip through because the gate crashed).

SCRATCH WORKSPACES: workspace_kind="scratch" (the kanban default) is a valid
gate target -- we do NOT skip on workspace kind; a scratch dir holding code is
gated normally. But a scratch dir is CLEANED UP by the dispatcher after the
card completes, so the per-run evidence under <ws>/.hermes/gate-runs/ and the
tier sidecar are EPHEMERAL -- they vanish with the workspace. We therefore emit
the gate VERDICT to the host log (a stable location) before returning, so a
post-completion audit still has the pass/fail + tier + stacks even once the
scratch dir is gone. (A full copy-evidence-to-stable-path is host-specific and
out of scope; the logged verdict is the durable minimum.)
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from . import classify
from . import gate
from . import tiers

logger = logging.getLogger(__name__)


def _field(task: Any, name: str, default: Any = None) -> Any:
    if task is None:
        return default
    if isinstance(task, dict):
        return task.get(name, default)
    return getattr(task, name, default)


def on_pre_kanban_complete(
    task: Any = None,
    config: Optional[dict] = None,
    **kwargs: Any,
) -> Optional[dict]:
    task_id = _field(task, "id", "")
    workspace_path = _field(task, "workspace_path")
    if not workspace_path:
        logger.warning(
            "quality-gate: card %s has no workspace_path; cannot gate (allowing)",
            task_id,
        )
        return None

    tier = classify.read_tier(workspace_path) or tiers.DEFAULT_TIER

    try:
        result = gate.evaluate_completion(workspace_path, tier, task_id=task_id)
    except Exception as exc:
        # Fail-closed: do not let a crash become a silent pass.
        logger.warning(
            "quality-gate: gate evaluation crashed for %s: %s", task_id, exc,
        )
        return {
            "action": "block",
            "message": (
                f"quality-gate could not be evaluated for card {task_id} "
                f"({exc}); blocking completion until the gate can run."
            ),
        }

    # Durable verdict to the host log (survives a scratch-dir cleanup).
    verdict = "PASS" if result.passed else "BLOCK"
    logger.info(
        "quality-gate VERDICT %s: card %s tier=%s stacks=%s hygiene_clean=%s",
        verdict, task_id, result.tier, ",".join(result.stacks) or "-",
        result.hygiene_clean,
    )

    if result.passed:
        return None

    return {"action": "block", "message": result.summary}
