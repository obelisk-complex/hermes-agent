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
