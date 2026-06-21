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


def register(ctx) -> None:
    def _spawn(**kwargs):
        config = _load_config()
        try:
            llm = ctx.llm
        except Exception:
            llm = None
        return spawn_hook.on_pre_kanban_spawn(config=config, llm=llm, **kwargs)

    def _blocked(**kwargs):
        config = _load_config()
        return blocked_hook.on_kanban_task_blocked(
            config=config,
            requeue=_bind_requeue(),
            update_field=_bind_update_field(),
            notify_sender=_matrix_sender(config),
            **kwargs,
        )

    def _complete(**kwargs):
        # NOTE: completion_hook.on_pre_kanban_complete has its OWN try/except
        # that converts a gate crash into a BLOCK dict. That is load-bearing:
        # invoke_hook (plugins.py) swallows a RAISED exception and excludes it
        # from the results list, which the fork consumer reads as "no block" =
        # fail-OPEN. So the fail-closed guarantee depends on the block being a
        # RETURNED dict, never an exception. Do not remove that inner guard.
        config = _load_config()
        return completion_hook.on_pre_kanban_complete(config=config, **kwargs)

    ctx.register_hook("pre_kanban_spawn", _spawn)
    ctx.register_hook("kanban_task_blocked", _blocked)
    ctx.register_hook("pre_kanban_complete", _complete)
    logger.info("quality-gate: registered 3 kanban hooks")
