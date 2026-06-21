"""on_kanban_task_blocked: escalate a retriably-blocked card up the ladder.

Fired by the fork's ``kanban_task_blocked`` observer hook. On a retriable
failure we requeue the card onto the next-stronger model rung (via the fork's
requeue_blocked_task, injected as ``requeue``). At the top rung the ladder is
exhausted and we stop. Matrix evidence is posted only when configured.

BEHAVIOUR CHANGE (named): setting max_retries=0 at ladder exhaustion is a
deliberate terminal-state change to prevent an infinite auto-requeue loop at
the top rung. The field name ``max_retries`` and the value ``0`` match the
dispatcher's retry semantics; if the fork uses a different terminal mechanism,
change BOTH the seam call and ``test_exhaustion_sets_terminal_signal`` together.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from . import ladder
from . import notify

logger = logging.getLogger(__name__)


def _field(task: Any, name: str, default: Any = None) -> Any:
    if task is None:
        return default
    if isinstance(task, dict):
        return task.get(name, default)
    return getattr(task, name, default)


def on_kanban_task_blocked(
    task: Any = None,
    reason: Optional[str] = None,
    config: Optional[dict] = None,
    requeue: Optional[Callable[..., Any]] = None,
    update_field: Optional[Callable[..., Any]] = None,
    notify_sender: Optional[Callable[..., Any]] = None,
    **kwargs: Any,
) -> None:
    task_id = _field(task, "id")
    current = _field(task, "model_override")

    if not ladder.is_retriable(reason):
        logger.warning(
            "quality-gate: block reason %r not retriable; not escalating %s",
            reason, task_id,
        )
        return

    lad = ladder.load_ladder(config)
    nxt = ladder.next_rung(current, lad)
    if nxt is None:
        # Ladder exhausted: a terminal state needing a human. Produce a
        # MACHINE-READABLE terminal signal so the card cannot loop forever
        # at the top rung (named behaviour change, see module docstring).
        logger.error(
            "quality-gate: card %s EXHAUSTED the model ladder (top rung %r); "
            "needs a human. Setting max_retries=0 to stop auto-requeue.",
            task_id, current,
        )
        try:
            if update_field is None:
                logger.warning(
                    "quality-gate: no update_field seam wired; cannot mark %s "
                    "terminal -- it may auto-requeue at the top rung", task_id,
                )
            else:
                # Stop the dispatcher auto-requeuing this card forever.
                update_field(task_id, "max_retries", 0)
        except Exception as exc:
            logger.warning(
                "quality-gate: could not set terminal signal for %s: %s", task_id, exc,
            )
        try:
            notify.notify(
                config,
                f"quality-gate: card {task_id} exhausted the model ladder "
                f"(top rung {current}); needs a human.",
                sender=notify_sender,
            )
        except Exception as exc:  # notify already swallows; belt and braces
            logger.warning("quality-gate: exhaustion notify failed: %s", exc)
        return

    try:
        if requeue is None:
            logger.warning(
                "quality-gate: no requeue callable wired; cannot escalate %s", task_id,
            )
        else:
            requeue(task_id, model_override=nxt)
            logger.info(
                "quality-gate: escalated %s from %r to %r (reason=%s)",
                task_id, current, nxt, reason,
            )
    except Exception as exc:
        logger.warning(
            "quality-gate: requeue/escalation failed for %s: %s", task_id, exc,
        )
        return

    try:
        notify.notify(
            config,
            f"quality-gate: card {task_id} blocked ({reason}); "
            f"escalated {current} -> {nxt}.",
            sender=notify_sender,
        )
    except Exception as exc:
        logger.warning("quality-gate: escalation notify failed: %s", exc)
