"""Sub-project D: reconstruct each model's tuned best-shot ModelSpec from the
best_settings.json record sub-project C persisted (schema_version 1).

The record snapshots the full base ModelSpec AND the winner SettingsProfile, so the
tuned spec round-trips as
  apply_profile(ModelSpec(**rec["base"]), SettingsProfile(**{k:v for k,v in rec["winner"].items()
                                                             if v is not None})).

Everything here is fail-loud + raise-safe: a missing record, an unreadable record, a base that
has DRIFTED from the current roster, or a reconstruction that apply_profile rejects (e.g. the roster
is now omit_temp=True but the winner carries a temperature) each fall back to the baseline roster
spec for BOTH phases and append a loud note, rather than crashing the whole dual run or silently
substituting a wrong spec.
"""
from __future__ import annotations

import json
import os

from .models import ModelSpec, SettingsProfile
from .tuning import apply_profile


def load_tuned_specs(settings_dir: str, roster: list, env: dict | None = None):
    """Return (specs_by_key, notes).

    For every spec in `roster`, look for <settings_dir>/<key>/best_settings.json and reconstruct the
    tuned best-shot ModelSpec; when it is absent/unreadable/incoherent, fall back to the baseline spec
    (so the best-shot phase runs at equal footing) and record a loud note. `env` is accepted for call
    parity with the other loaders and reserved for future gateway-aware validation; it is not read here.
    """
    specs_by_key: dict = {}
    notes: list = []
    for spec in roster:
        key = spec.key
        path = os.path.join(settings_dir, key, "best_settings.json")
        if not os.path.isfile(path):
            specs_by_key[key] = spec
            notes.append(f"note: no tuned record for {key} at {path}; using the baseline spec for "
                         "the best-shot phase (equal footing)")
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                rec = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            specs_by_key[key] = spec
            notes.append(f"WARNING: could not read tuned record for {key} ({e}); using the baseline spec")
            continue

        # Base-drift guard (D3, fail loud): the record's base ModelSpec must equal the CURRENT roster
        # spec. On drift, warn and reconstruct against the current roster spec (the runtime truth).
        base = spec
        try:
            recorded_base = ModelSpec(**rec["base"])
        except (TypeError, KeyError) as e:
            specs_by_key[key] = spec
            notes.append(f"WARNING: tuned record for {key} has an unreadable base ({e}); "
                         "using the baseline spec")
            continue
        if recorded_base != spec:
            notes.append(f"WARNING: tuned record base for {key} has drifted from the current roster "
                         "spec; using the CURRENT roster spec as the base for reconstruction")
        else:
            base = recorded_base

        winner = rec.get("winner") or {}
        profile = SettingsProfile(**{k: v for k, v in winner.items() if v is not None})
        try:
            specs_by_key[key] = apply_profile(base, profile)
        except ValueError as e:
            # e.g. the roster is now omit_temp=True but the winner set a sampling knob.
            specs_by_key[key] = base
            notes.append(f"WARNING: could not reconstruct tuned spec for {key} ({e}); falling back to "
                         "the baseline spec for BOTH phases")
    return specs_by_key, notes
