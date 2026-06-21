"""Optional, feature-flagged Matrix notify.

By DEFAULT this is a no-op: no Matrix client is imported, no network call is
made, no third-party service is wired. It activates ONLY when config sets
``quality_gate.matrix.enabled: true`` AND a ``room`` — at which point the
caller-supplied ``sender`` callable performs the actual send. This keeps the
plugin free of any hard dependency on a live Matrix server (operator policy:
no unilateral third-party integrations).
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


def _matrix_cfg(config: Optional[dict]) -> dict:
    if isinstance(config, dict):
        qg = config.get("quality_gate")
        if isinstance(qg, dict):
            m = qg.get("matrix")
            if isinstance(m, dict):
                return m
    return {}


def home_room(config: Optional[dict]) -> Optional[str]:
    room = _matrix_cfg(config).get("room")
    return room if isinstance(room, str) and room else None


def matrix_enabled(config: Optional[dict]) -> bool:
    """True only when explicitly enabled AND a room is configured."""
    m = _matrix_cfg(config)
    return bool(m.get("enabled")) and bool(home_room(config))


def notify(
    config: Optional[dict],
    text: str,
    *,
    sender: Optional[Callable[..., Any]] = None,
) -> bool:
    """Send *text* to the configured Matrix room via *sender*, if enabled.

    No-op (returns False) when Matrix is not configured. Never raises.
    """
    if not matrix_enabled(config):
        logger.debug("quality-gate: Matrix notify disabled; skipping")
        return False
    if sender is None:
        logger.warning("quality-gate: Matrix enabled but no sender wired; skipping")
        return False
    m = _matrix_cfg(config)
    room = home_room(config)
    token = m.get("token")
    try:
        result = sender(room, text, token)
        return bool(result) if result is not None else True
    except Exception as exc:
        logger.warning("quality-gate: Matrix notify failed: %s", exc)
        return False
