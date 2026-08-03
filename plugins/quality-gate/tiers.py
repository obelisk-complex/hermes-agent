"""Tiered rigor: which gate kinds run at each tier.

quick     -> lint only (fast pre-flight)
standard  -> lint + test (the default)
thorough  -> lint + test + typecheck + build (release-grade)
"""
from __future__ import annotations

from typing import Optional, Tuple

TIERS: Tuple[str, ...] = ("quick", "standard", "thorough")
DEFAULT_TIER = "standard"

_TIER_KINDS = {
    "quick": ("lint",),
    "standard": ("lint", "test"),
    "thorough": ("lint", "test", "typecheck", "build"),
}


def normalise_tier(value: Optional[str]) -> str:
    """Return a valid tier name, defaulting to DEFAULT_TIER."""
    if not value:
        return DEFAULT_TIER
    v = str(value).strip().lower()
    return v if v in _TIER_KINDS else DEFAULT_TIER


def kinds_for_tier(tier: str) -> Tuple[str, ...]:
    """Return the gate kinds that run at *tier* (fallback: DEFAULT_TIER)."""
    return _TIER_KINDS[normalise_tier(tier)]
