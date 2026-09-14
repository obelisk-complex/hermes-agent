"""Static model escalation ladder, read from config (NO grader dependency).

The ladder is ``quality_gate.model_ladder`` (a plain list, weakest first). If
absent, DEFAULT_LADDER is used. ``initial_rung`` caps the starting rung one
below the top so a top-model card can still escalate at least once.
"""
from __future__ import annotations

import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

# Weakest -> strongest. Overridden by config quality_gate.model_ladder.
DEFAULT_LADDER: List[str] = [
    "claude-3-5-haiku",
    "claude-sonnet-4-5",
    "claude-opus-4-8",
]

# Failure reasons that warrant an escalation/requeue (vs a hard stop).
RETRIABLE_FAILURES = frozenset({
    "timeout", "rate_limit", "overloaded", "connection",
    "gate_failed", "crashed", "timed_out",
})


def _dedupe(seq: List[str]) -> List[str]:
    seen: set = set()
    out: List[str] = []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def load_ladder(config: Optional[dict]) -> List[str]:
    """Return the configured ladder, or DEFAULT_LADDER, de-duped in order."""
    if isinstance(config, dict):
        qg = config.get("quality_gate")
        if isinstance(qg, dict):
            raw = qg.get("model_ladder")
            if isinstance(raw, list) and raw and all(isinstance(x, str) for x in raw):
                return _dedupe(list(raw))
    return list(DEFAULT_LADDER)


def next_rung(current: Optional[str], ladder: List[str]) -> Optional[str]:
    """The next stronger rung after *current*; None if already at the top.

    Unknown/None current -> the first rung. A NON-None current that is not on
    the ladder is logged at WARNING first: it usually means the operator changed
    the ladder under a running card, and falling back to ladder[0] is a
    downgrade-escalation that must be observable (fail-loud), not silent.
    """
    if not ladder:
        return None
    if current in ladder:
        idx = ladder.index(current)
        return ladder[idx + 1] if idx + 1 < len(ladder) else None
    if current is not None:
        logger.warning(
            "quality-gate: current model %r is not on the configured ladder %r; "
            "falling back to the weakest rung %r (a downgrade-escalation); "
            "check quality_gate.model_ladder", current, ladder, ladder[0],
        )
    return ladder[0]


def initial_rung_for_tier(ladder: List[str], tier: Optional[str]) -> str:
    """Tier-aware starting rung, capped at len-2 so one escalation always remains.

    quick    -> weakest rung   (ladder[0])
    thorough -> near-top rung   (ladder[len-2], i.e. the cap)
    standard -> middle rung      (ladder[min((len-1)//2, cap)])
    Unknown / None tier         -> treated as standard.

    The cap (len-2) guarantees the returned rung is never the very top when
    len >= 2, so at least one escalation always remains.

    NOTE (intentional, exercised by tests): for short ladders the tiers
    compress. len==2 collapses all tiers onto ladder[0]; len==3 collapses
    standard and thorough onto ladder[1]. Genuine 3-way differentiation needs
    len >= 4.
    """
    if not ladder:
        return ""
    cap_idx = max(0, len(ladder) - 2)
    t = (tier or "").strip().lower()
    if t == "quick":
        idx = 0
    elif t == "thorough":
        idx = cap_idx
    else:  # standard / unknown -> middle, never above the cap
        idx = max(0, min((len(ladder) - 1) // 2, cap_idx))
    return ladder[idx]


def initial_rung(ladder: List[str]) -> str:
    """Backward-compatible default starting rung (the 'thorough' rung).

    Retained for existing callers/tests; delegates to ``initial_rung_for_tier``
    so the cap logic has one source of truth. Identical ladder[len-2] semantics
    to the pre-tier-aware version for every ladder length.
    """
    return initial_rung_for_tier(ladder, "thorough")


def is_retriable(reason: Optional[str]) -> bool:
    return bool(reason) and reason in RETRIABLE_FAILURES
