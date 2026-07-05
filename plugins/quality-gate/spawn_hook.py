"""on_pre_kanban_spawn: classify the card's tier and pick the initial model rung.

Fired by the fork's ``pre_kanban_spawn`` hook before a worker is dispatched.
Returns a dict of task-field overrides (``model_override``) applied before
spawn, or None to leave the card unchanged. Review/terminal cards are guarded
out of classification.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from . import classify
from . import ladder
from . import notify
from . import tiers

logger = logging.getLogger(__name__)


def _field(task: Any, name: str, default: Any = None) -> Any:
    if task is None:
        return default
    if isinstance(task, dict):
        return task.get(name, default)
    return getattr(task, name, default)


def on_pre_kanban_spawn(
    task: Any = None,
    config: Optional[dict] = None,
    llm: Any = None,
    **kwargs: Any,
) -> Optional[dict]:
    status = _field(task, "status", "")
    kind = _field(task, "kind", _field(task, "workspace_kind", ""))
    if not classify.should_classify(status, kind):
        logger.debug("quality-gate: spawn hook skipping review/terminal card %s", _field(task, "id"))
        return None

    # Re-spawn guard: a card that already carries a model_override has been
    # escalated up the ladder by blocked_hook (requeue(..., model_override=nxt))
    # and re-queued (status flips back to an actionable value). Recomputing the
    # initial rung here would RESET that escalation back down the ladder, so
    # leave the escalated card untouched (and skip the tier re-classification
    # LLM call). The tier sidecar from the first spawn already persists.
    if _field(task, "model_override"):
        logger.debug(
            "quality-gate: spawn hook leaving escalated card %s on model %r",
            _field(task, "id"), _field(task, "model_override"),
        )
        return None

    title = _field(task, "title", "") or ""
    body = _field(task, "body", "") or ""
    workspace_path = _field(task, "workspace_path")

    if llm is not None:
        tier = classify.classify_tier(title, body, llm=llm)
    else:
        tier = tiers.DEFAULT_TIER

    if workspace_path:
        try:
            classify.write_tier(workspace_path, tier)
        except OSError as exc:
            logger.warning("quality-gate: could not write tier sidecar: %s", exc)

    lad = ladder.load_ladder(config)
    rung = ladder.initial_rung_for_tier(lad, tier)

    if notify.matrix_enabled(config):
        logger.info(
            "quality-gate: Matrix home room %s active for card %s",
            notify.home_room(config), _field(task, "id"),
        )

    if not rung:
        return None
    return {"model_override": rung, "tier": tier}
