"""Sub-project C: settings-override profiles + tuning driver. Offline: injected transport +
gateway resolver + temp corpora, mirroring tests/test_runner.py."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from tools.model_bakeoff import tuning
from tools.model_bakeoff.models import ModelSpec, SettingsProfile
from tools.model_bakeoff.tests.test_runner import make_task, transport_returning

PASS_CODE = "```python\ndef f(x):\n    return x + 1\n```"   # passes make_task's oracle (f(2)==3)


def _resolver(ok=True):
    return lambda gw: SimpleNamespace(gateway=gw, base_url=("https://x/v1" if ok else None),
                                      api_key=("k" if ok else None), ok=ok)


def _passing_transport(out_tokens=120):
    return transport_returning(PASS_CODE, usage={"prompt_tokens": 10, "completion_tokens": out_tokens})


def _maxtokens_gated_transport(threshold):
    # passes only when the payload's max_tokens >= threshold, else returns a wrong answer
    async def t(url, headers, json, timeout):
        code = PASS_CODE if json.get("max_tokens", 0) >= threshold else "```python\ndef f(x):\n    return x + 99\n```"
        return 200, {"choices": [{"message": {"content": code}}],
                     "usage": {"prompt_tokens": 10, "completion_tokens": 50}}, 0.2
    return t


async def _http503_transport(url, headers, json, timeout):
    return 503, {"error": "busy"}, 0.2


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


# --- Task 3: evaluate_profile over dev tasks ---

def test_evaluate_profile_counts_passes_and_tokens(tmp_path):
    task = make_task(tmp_path, "t1")
    ev = asyncio.run(tuning.evaluate_profile(
        _spec(), SettingsProfile(max_tokens=32000), [task], _resolver(), _passing_transport(120),
        repeats=2, sandbox_timeout=30))
    assert ev.n_runs == 2 and ev.n_passed == 2 and ev.n_operational == 0
    assert ev.mean_output_tokens == 120 and ev.spec.max_tokens == 32000 and ev.sample_error == ""


def test_evaluate_profile_operational_failures(tmp_path):
    task = make_task(tmp_path, "t2")
    ev = asyncio.run(tuning.evaluate_profile(
        _spec(), SettingsProfile(), [task], _resolver(), _http503_transport, repeats=2, sandbox_timeout=30))
    assert ev.n_operational == 2 and ev.pass_fraction == 0.0
    assert ev.mean_output_tokens is None and "503" in ev.sample_error   # None, not a fake 0


def test_evaluate_profile_raises_on_unconfigured_gateway(tmp_path):
    task = make_task(tmp_path, "t3")
    with pytest.raises(ValueError):
        asyncio.run(tuning.evaluate_profile(
            _spec(), SettingsProfile(), [task], _resolver(ok=False), _passing_transport(),
            repeats=1, sandbox_timeout=30))


def test_evaluate_profile_raises_on_metered_gateway(tmp_path):
    task = make_task(tmp_path, "t4")
    with pytest.raises(ValueError):   # never issue a live metered call during tuning
        asyncio.run(tuning.evaluate_profile(
            _spec(), SettingsProfile(gateway="opencode-zen", wire_id="x"), [task], _resolver(),
            _passing_transport(), repeats=1, sandbox_timeout=30))
