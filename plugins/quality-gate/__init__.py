"""quality-gate plugin entry -- mechanical gate + classifier/escalation.

Registers three kanban hooks supplied by the fork-edits plan:
  * pre_kanban_spawn    -> classify tier, pick initial model rung
  * kanban_task_blocked -> escalate up the static model ladder via requeue
  * pre_kanban_complete -> run the mechanical gate, BLOCK on failure

All host wiring (live config, ctx.llm, the fork's requeue_blocked_task,
optional Matrix sender) is resolved lazily inside the adapters so an absent
fork edit or unreadable config degrades to defaults rather than breaking load.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from . import blocked_hook, completion_hook, notify, spawn_hook

logger = logging.getLogger(__name__)


def _load_config() -> dict:
    try:
        from hermes_cli.config import load_config
        cfg = load_config()
        return cfg if isinstance(cfg, dict) else {}
    except Exception as exc:
        logger.warning("quality-gate: could not load config (%s); using defaults", exc)
        return {}


def _bind_requeue() -> Optional[Callable[..., Any]]:
    """Bind the fork's requeue_blocked_task, wrapping the conn argument.

    The real kanban_db function signature is
    ``requeue_blocked_task(conn, task_id, *, model_override=None, reason=None)``.
    We expose a thin wrapper that opens a connection internally so callers
    (and injection seam tests) only need ``(task_id, model_override=...)``.
    """
    try:
        from hermes_cli import kanban_db
        def _requeue(task_id: str, *, model_override: Optional[str] = None, **kw: Any) -> bool:
            conn = kanban_db.connect()
            try:
                return kanban_db.requeue_blocked_task(conn, task_id, model_override=model_override)
            finally:
                conn.close()
        return _requeue
    except Exception as exc:
        logger.warning(
            "quality-gate: requeue_blocked_task unavailable (%s); "
            "escalation disabled until the fork edit lands", exc,
        )
        return None


def _bind_update_field() -> Optional[Callable[..., Any]]:
    """Bind the fork's update_task_field, used to set the terminal signal
    (max_retries=0) when the model ladder is exhausted."""
    try:
        from hermes_cli import kanban_db
        def _update_field(task_id: str, field: str, value: Any) -> bool:
            conn = kanban_db.connect()
            try:
                return kanban_db.update_task_field(conn, task_id, field, value)
            finally:
                conn.close()
        return _update_field
    except Exception as exc:
        logger.warning(
            "quality-gate: update_task_field unavailable (%s); ladder-exhaustion "
            "terminal signal disabled until the fork edit lands", exc,
        )
        return None


def _matrix_sender(config: dict) -> Optional[Callable[..., Any]]:
    if not notify.matrix_enabled(config):
        return None

    def _send(room: str, text: str, token: Optional[str]) -> bool:
        # Host-specific Matrix client wiring lives here in production. The
        # feature flag guarantees this is only reached when the operator
        # has explicitly configured quality_gate.matrix.{enabled,room}.
        logger.info("quality-gate: [matrix:%s] %s", room, text)
        return True

    return _send


def _bind_get_model_override() -> Optional[Callable[[str], Optional[str]]]:
    """Return a callable that looks up the current model_override for a task.

    The fork's ``kanban_task_blocked`` fire does NOT include ``model_override``
    (it is absent from both the manual and auto-block kwargs dicts). We resolve
    it at hook-fire time by querying the DB directly, mirroring the
    connect/try/finally-close pattern used by ``_bind_requeue`` and
    ``_bind_update_field``.
    """
    try:
        from hermes_cli import kanban_db
        def _get(task_id: str) -> Optional[str]:
            conn = kanban_db.connect()
            try:
                task = kanban_db.get_task(conn, task_id)
                return task.model_override if task is not None else None
            finally:
                conn.close()
        return _get
    except Exception as exc:
        logger.warning(
            "quality-gate: get_task unavailable (%s); "
            "model_override lookup disabled -- escalation will start from "
            "the weakest ladder rung", exc,
        )
        return None


# Mapping from the fork's ``trigger`` kwarg to a token that
# ``ladder.is_retriable`` accepts (or rejects for manual blocks).
#
# Design choice (named, per CLAUDE.md): we do NOT pass the free-text
# ``reason`` field from the fork to ``is_retriable`` -- that field is an
# error[:500] truncation for auto_block, or a human-supplied string for
# manual blocks; neither is guaranteed to match RETRIABLE_FAILURES.
#
# Instead the adapter maps the CLEAN structural ``trigger`` signal:
#   trigger="auto_block"  -> "gate_failed"   (in RETRIABLE_FAILURES -> escalates)
#   trigger="manual"      -> "permission_denied" (NOT in set -> no escalation)
#   trigger=<unknown>     -> "permission_denied" (fail-safe: no escalation)
#
# This is safe because auto_block is fired only when the dispatcher's own
# circuit breaker trips (a runaway failure loop), which is exactly the case
# where a stronger model is warranted. Manual blocks are human decisions and
# must not be auto-escalated.
_TRIGGER_TO_RETRIABLE: dict = {
    "auto_block": "gate_failed",
}
_NON_RETRIABLE_SENTINEL = "permission_denied"


def register(ctx) -> None:
    def _spawn(**kwargs):
        # ADAPTER: fork fires pre_kanban_spawn with FLAT kwargs (no task= object).
        # Build the task dict that spawn_hook.on_pre_kanban_spawn reads via
        # _field(task, ...). The fork does not send ``status`` in its fire, so
        # ``should_classify`` will not skip on status (acceptable -- the review/
        # terminal skip guard in classify.should_classify will use an empty string
        # for status, defaulting to non-skip behaviour).
        task = {
            "id": kwargs.get("task_id"),
            "title": kwargs.get("title"),
            "body": kwargs.get("body"),
            "assignee": kwargs.get("assignee"),
            "model_override": kwargs.get("model_override"),
            "workspace_path": kwargs.get("workspace_path"),
            "workspace_kind": kwargs.get("workspace_kind"),
            "branch_name": kwargs.get("branch_name"),
            "priority": kwargs.get("priority"),
            "skills": kwargs.get("skills"),
        }
        config = _load_config()
        try:
            llm = ctx.llm
        except Exception:
            llm = None
        return spawn_hook.on_pre_kanban_spawn(task=task, config=config, llm=llm)

    def _blocked(**kwargs):
        # ADAPTER: fork fires kanban_task_blocked with FLAT kwargs (no task= object)
        # and does NOT include model_override. We look it up from the DB via the
        # bound getter and build the task dict. We also normalise retriability via
        # trigger (structural) rather than reason (free text) -- see
        # _TRIGGER_TO_RETRIABLE above.
        task_id = kwargs.get("task_id")
        get_model_override = _bind_get_model_override()
        model_override: Optional[str] = None
        if get_model_override is not None and task_id:
            try:
                model_override = get_model_override(task_id)
            except Exception as exc:
                logger.warning(
                    "quality-gate: could not look up model_override for %s (%s); "
                    "escalation will proceed from weakest rung", task_id, exc,
                )
        task = {
            "id": task_id,
            "model_override": model_override,
        }
        # Map the fork's structural trigger signal to a retriability token.
        trigger = kwargs.get("trigger", "")
        mapped_reason = _TRIGGER_TO_RETRIABLE.get(trigger, _NON_RETRIABLE_SENTINEL)
        config = _load_config()
        return blocked_hook.on_kanban_task_blocked(
            task=task,
            reason=mapped_reason,
            config=config,
            requeue=_bind_requeue(),
            update_field=_bind_update_field(),
            notify_sender=_matrix_sender(config),
        )

    def _complete(**kwargs):
        # ADAPTER: fork fires pre_kanban_complete with FLAT kwargs (no task= object).
        # Build the task dict that completion_hook.on_pre_kanban_complete reads via
        # _field(task, ...).
        #
        # NOTE: completion_hook.on_pre_kanban_complete has its OWN try/except
        # that converts a gate crash into a BLOCK dict. That is load-bearing:
        # invoke_hook (plugins.py) swallows a RAISED exception and excludes it
        # from the results list, which the fork consumer reads as "no block" =
        # fail-OPEN. So the fail-closed guarantee depends on the block being a
        # RETURNED dict, never an exception. Do not remove that inner guard.
        task = {
            "id": kwargs.get("task_id"),
            "workspace_path": kwargs.get("workspace_path"),
            "branch_name": kwargs.get("branch_name"),
            "model_override": kwargs.get("model_override"),
        }
        config = _load_config()
        return completion_hook.on_pre_kanban_complete(task=task, config=config)

    ctx.register_hook("pre_kanban_spawn", _spawn)
    ctx.register_hook("kanban_task_blocked", _blocked)
    ctx.register_hook("pre_kanban_complete", _complete)
    logger.info("quality-gate: registered 3 kanban hooks")
