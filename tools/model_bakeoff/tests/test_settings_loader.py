"""settings_loader.load_tuned_specs (sub-project D Task 1): reconstruct the tuned
best-shot ModelSpec from a best_settings.json record, with a base-drift guard and a
raise-safe fallback. Offline, pure: hand-written record fixtures, no live calls."""
from __future__ import annotations

import dataclasses
import json
import os

from tools.model_bakeoff import settings_loader
from tools.model_bakeoff.models import ModelSpec, SettingsProfile


def _spec(**kw) -> ModelSpec:
    base = dict(key="m", gateway="opencode-go", wire_id="m", cost_model="subscription",
                reasoning=False, omit_temp=False, max_tokens=8000, api_timeout_s=180)
    base.update(kw)
    return ModelSpec(**base)


def _write_record(settings_dir, spec, winner: SettingsProfile, base_spec=None):
    """Write a best_settings.json mirroring tuning._record's schema (schema_version 1)."""
    base_spec = base_spec if base_spec is not None else spec
    d = os.path.join(settings_dir, spec.key)
    os.makedirs(d, exist_ok=True)
    rec = {"schema_version": 1, "model_key": spec.key,
           "base": dataclasses.asdict(base_spec),
           "winner": dataclasses.asdict(winner),
           "low_confidence": False, "reasons": [], "caveats": []}
    with open(os.path.join(d, "best_settings.json"), "w", encoding="utf-8") as f:
        json.dump(rec, f)


def test_reconstructs_tuned_spec_from_record(tmp_path):
    spec = _spec(key="glm-x", max_tokens=8000)
    _write_record(str(tmp_path), spec, SettingsProfile(max_tokens=32000, temperature=0.7))
    specs, notes = settings_loader.load_tuned_specs(str(tmp_path), [spec])
    assert specs["glm-x"].max_tokens == 32000        # winner overlaid
    assert specs["glm-x"].temperature == 0.7


def test_missing_record_falls_back_to_baseline_with_note(tmp_path):
    spec = _spec(key="untuned")
    specs, notes = settings_loader.load_tuned_specs(str(tmp_path), [spec])
    assert specs["untuned"] == spec                   # baseline spec unchanged
    assert any("untuned" in n and "no tuned record" in n for n in notes)


def test_base_drift_warns_and_uses_current_roster_spec(tmp_path):
    # the record's base has max_tokens=8000; the CURRENT roster spec drifted to 16000.
    recorded_base = _spec(key="drift", max_tokens=8000)
    current = _spec(key="drift", max_tokens=16000)
    _write_record(str(tmp_path), current, SettingsProfile(temperature=0.5), base_spec=recorded_base)
    specs, notes = settings_loader.load_tuned_specs(str(tmp_path), [current])
    assert specs["drift"].max_tokens == 16000         # winner applied to the CURRENT spec, not the stale base
    assert specs["drift"].temperature == 0.5
    assert any("drift" in n.lower() and "roster spec" in n for n in notes)


def test_all_none_winner_yields_baseline(tmp_path):
    spec = _spec(key="noop")
    _write_record(str(tmp_path), spec, SettingsProfile())
    specs, notes = settings_loader.load_tuned_specs(str(tmp_path), [spec])
    assert specs["noop"] == spec                       # identity profile -> baseline


def test_incoherent_reconstruction_is_caught_and_falls_back(tmp_path):
    # roster spec is now omit_temp=True but the recorded winner carries a temperature ->
    # apply_profile raises ValueError -> caught, baseline used for BOTH phases, loud note.
    spec = _spec(key="reasoner", omit_temp=True, temperature=None)
    _write_record(str(tmp_path), spec, SettingsProfile(temperature=0.7))
    specs, notes = settings_loader.load_tuned_specs(str(tmp_path), [spec])
    assert specs["reasoner"] == spec                   # baseline fallback for both phases
    assert any("reasoner" in n and "baseline" in n.lower() for n in notes)
