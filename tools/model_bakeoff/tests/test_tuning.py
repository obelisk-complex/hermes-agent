"""Sub-project C: settings-override profiles + tuning driver. Offline: injected transport +
gateway resolver + temp corpora, mirroring tests/test_runner.py."""
from __future__ import annotations

import pytest

from tools.model_bakeoff import tuning
from tools.model_bakeoff.models import ModelSpec, SettingsProfile


def _spec(**kw):
    base = dict(key="m", gateway="opencode-go", wire_id="m", cost_model="subscription",
                reasoning=False, omit_temp=False, max_tokens=8000, api_timeout_s=180)
    base.update(kw)
    return ModelSpec(**base)


# --- Task 2: apply_profile + validation ---

def test_apply_overrides_only_set_fields():
    out = tuning.apply_profile(_spec(), SettingsProfile(max_tokens=32000, temperature=0.5))
    assert out.max_tokens == 32000 and out.temperature == 0.5
    assert out.api_timeout_s == 180 and out.wire_id == "m"   # inherited


def test_apply_empty_profile_is_identity():
    s = _spec()
    assert tuning.apply_profile(s, SettingsProfile()) == s


def test_gateway_override_requires_wire_id():
    with pytest.raises(ValueError):
        tuning.apply_profile(_spec(), SettingsProfile(gateway="ollama-cloud"))


def test_gateway_with_wire_id_ok():
    out = tuning.apply_profile(_spec(), SettingsProfile(gateway="ollama-cloud", wire_id="m:cloud"))
    assert out.gateway == "ollama-cloud" and out.wire_id == "m:cloud"


def test_sampling_invalid_under_omit_temp():
    with pytest.raises(ValueError):
        tuning.apply_profile(_spec(omit_temp=True), SettingsProfile(temperature=0.7))


def test_apply_does_not_mutate_or_share_reasoning_extras():
    s = _spec(reasoning_extras={"thinking": {"type": "enabled"}})
    out = tuning.apply_profile(s, SettingsProfile(reasoning_extras={"thinking": {"type": "disabled"}}))
    assert s.reasoning_extras == {"thinking": {"type": "enabled"}}   # original untouched
    assert out.reasoning_extras == {"thinking": {"type": "disabled"}}
