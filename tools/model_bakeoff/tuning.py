"""Sub-project C: per-model best-shot settings tuning.

A SettingsProfile is layered over a ModelSpec via apply_profile (no roster edits); a
coordinate-ascent hill-climb searches the profile space on a held-out dev set (objective = oracle
pass-rate). The search is SINGLE-SEED coordinate ascent: it finds a LOCAL optimum, is
order-dependent, and on a flat objective the winner among ties is decided only by the token/latency
tie-break. Offline-testable: the transport AND the gateway connection RESOLVER are injected, exactly
as runner is. Tuning is subscription-only: metered gateways are refused (there is no budget cap
here).
"""
from __future__ import annotations

import dataclasses

from .models import ModelSpec, SettingsProfile

# Per registry.py: opencode-zen is the ONLY pay-as-you-go gateway. Tuning must never issue a live
# metered call, so any candidate whose (tuned) gateway is metered is pruned/refused.
METERED_GATEWAYS = frozenset({"opencode-zen"})


def apply_profile(spec: ModelSpec, profile: SettingsProfile) -> ModelSpec:
    """Return a NEW ModelSpec with the profile's non-None fields overlaid (spec is never mutated).
    Raises ValueError on an incoherent override: a gateway change without a matching wire_id, or a
    sampling knob set on an omit_temp model (reasoning models that omit temperature reject them)."""
    overrides = {f.name: getattr(profile, f.name)
                 for f in dataclasses.fields(profile) if getattr(profile, f.name) is not None}
    if "gateway" in overrides and "wire_id" not in overrides:
        raise ValueError(f"profile overrides gateway to {overrides['gateway']!r} without a wire_id; "
                         "the same model has a different wire id per gateway, so both are required")
    new = dataclasses.replace(spec, **overrides)
    if new.omit_temp and any(v is not None for v in (new.temperature, new.top_p, new.top_k)):
        raise ValueError(f"model {new.key} has omit_temp=True but the profile sets sampling knobs "
                         "(temperature/top_p/top_k); reasoning models that omit temperature reject them")
    return new
