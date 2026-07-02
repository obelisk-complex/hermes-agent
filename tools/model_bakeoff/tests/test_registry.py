"""Roster integrity (SPEC §3): the contracts the rest of the build relies on.
Offline, no API, no network."""
from __future__ import annotations

import pytest

from tools.model_bakeoff import registry
from tools.model_bakeoff.models import ModelSpec


def test_keys_unique():
    keys = [m.key for m in registry.ROSTER]
    assert len(keys) == len(set(keys)), "roster keys must be unique"


def test_exactly_one_ceiling_and_it_is_opus_on_zen():
    ceilings = [m for m in registry.ROSTER if m.is_ceiling]
    assert len(ceilings) == 1
    c = registry.ceiling()
    assert c.key == "claude-opus-4-8"
    assert c.gateway == "opencode-zen"
    assert c.is_metered


def test_only_zen_is_metered():
    # SPEC §8: opencode-go and ollama-cloud are subscriptions ($0 marginal);
    # opencode-zen is the only metered gateway.
    for m in registry.ROSTER:
        if m.gateway == "opencode-zen":
            assert m.cost_model == "metered", f"{m.key} on zen should be metered"
        else:
            assert m.cost_model == "subscription", f"{m.key} off zen should be subscription"


def test_metered_helper_matches_zen():
    assert {m.key for m in registry.metered()} == {
        m.key for m in registry.ROSTER if m.gateway == "opencode-zen"
    }


def test_reasoning_split_partitions_roster():
    reasoning, non = registry.reasoning_split()
    assert len(reasoning) + len(non) == len(registry.ROSTER)
    assert not ({m.key for m in reasoning} & {m.key for m in non})


def test_deepseek_v4_flash_is_thinking_with_temp_omitted():
    # SPEC §3 PM3: codebase classifies deepseek-v4-flash as a thinking model.
    m = registry.by_key("deepseek-v4-flash")
    assert m.reasoning is True
    assert m.omit_temp is True


def test_deepseek_models_carry_thinking_reasoning_extras():
    # SPEC §3 run-blocker B (coverage/regression): deepseek thinking models must send the
    # thinking-enable control; guards a future refactor silently stripping the field.
    for key in ("deepseek-v4-flash", "deepseek-v4-pro"):
        assert registry.by_key(key).reasoning_extras == {"thinking": {"type": "enabled"}}


def test_minimax_m3_requires_preflight_live_test():
    # SPEC §3 PM4: zen routing for MiniMax M3 is unverified -> live-test at preflight.
    assert registry.by_key("minimax-m3").preflight_live_test is True


def test_metered_models_either_priced_or_flagged_for_verification():
    # A metered model must either carry pricing or be flagged for preflight wire-id
    # verification (where pricing is resolved). No silent $0 for a metered model.
    for m in registry.metered():
        priced = m.price_in_per_m is not None and m.price_out_per_m is not None
        assert priced or m.verify_wire_id, f"{m.key}: metered but no price and no verify flag"


def test_by_key_raises_on_unknown():
    with pytest.raises(KeyError):
        registry.by_key("no-such-model")


def test_all_specs_have_sane_bounds():
    for m in registry.ROSTER:
        assert m.max_tokens >= 4000
        assert m.api_timeout_s >= 60
        assert isinstance(m, ModelSpec)


# --- Coding bakeoff (Task 3): qwen3.7-max-go, sticker prices, judge_spec ---

CANDIDATES = ["deepseek-v4-pro", "deepseek-v4-flash", "glm-5.2", "qwen3.7-max-go"]


def test_qwen37_max_go_entry_fields():
    m = registry.by_key("qwen3.7-max-go")
    assert m.gateway == "opencode-go"       # subscription endpoint the user verified
    assert m.wire_id == "qwen3.7-max"       # same served model as the zen entry
    assert m.cost_model == "subscription"
    assert m.reasoning is True and m.omit_temp is True
    assert m.max_tokens == 16000 and m.api_timeout_s == 240
    assert m.verify_wire_id is True
    assert m.price_out_per_m == 3.75


def test_two_qwen_keys_differ_in_gateway():
    # The zen qwen3.7-max (metered) and the go qwen3.7-max-go (subscription) are distinct
    # roster keys sharing one wire_id; the bakeoff uses the go one so spend stays $0.
    assert registry.by_key("qwen3.7-max").gateway == "opencode-zen"
    assert registry.by_key("qwen3.7-max-go").gateway == "opencode-go"


def test_four_candidates_carry_sticker_output_prices():
    expected = {"deepseek-v4-flash": 0.28, "deepseek-v4-pro": 3.48,
                "glm-5.2": 4.40, "qwen3.7-max-go": 3.75}
    for key, price in expected.items():
        assert registry.by_key(key).price_out_per_m == price, key


def test_four_candidates_share_output_budget_and_timeout():
    # A2 (max_tokens parity) + A11 (timeout parity): remove fairness/timing confounds so the
    # thorough tier is judged on equal footing.
    for key in CANDIDATES:
        m = registry.by_key(key)
        assert m.max_tokens == 16000, f"{key} max_tokens"
        assert m.api_timeout_s == 240, f"{key} api_timeout_s"


def test_judge_spec_is_cross_family_zen_priced_and_not_in_roster():
    js = registry.judge_spec()
    assert js.key == "claude-sonnet"
    assert js.gateway == "opencode-zen"
    assert js.wire_id == "claude-sonnet-4-6"
    assert js.cost_model == "metered" and js.is_metered
    assert js.price_in_per_m == 3.0 and js.price_out_per_m == 15.0
    assert js.verify_wire_id is True
    assert js.key not in {m.key for m in registry.ROSTER}  # judge is intentionally NOT a candidate


# --- qwen coder models on Ollama Cloud (substitute qwen slot; qwen3.7-max/-plus unavailable) ---

def test_qwen_coder_ollama_entries():
    for key, wire in [("qwen3-coder-480b", "qwen3-coder:480b"),
                      ("qwen3-coder-next", "qwen3-coder-next")]:
        m = registry.by_key(key)
        assert m.gateway == "ollama-cloud"
        assert m.wire_id == wire
        assert m.cost_model == "subscription"
        assert m.reasoning is False and m.omit_temp is False
        assert m.max_tokens == 16000 and m.api_timeout_s == 300
        assert m.verify_wire_id is True
        assert m.preflight_live_test is True   # Ollama Cloud correlated-outage defence
        assert m.price_out_per_m is None       # subscription open-weight; parity with qwen3.5-397b
