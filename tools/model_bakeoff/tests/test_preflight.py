"""preflight: gateway checks + the two audited LOWs + minimax live-test
(SPEC §10). Offline: fake chat transport + injected served-id map."""
from __future__ import annotations

import asyncio

from tools.model_bakeoff import preflight
from tools.model_bakeoff.models import ModelSpec

GO = ModelSpec(key="go", gateway="opencode-go", wire_id="go-wire",
               cost_model="subscription", reasoning=False, omit_temp=False,
               max_tokens=100, api_timeout_s=30)
ZEN_R = ModelSpec(key="zenR", gateway="opencode-zen", wire_id="zen-wire",
                  cost_model="metered", reasoning=True, omit_temp=False,
                  max_tokens=100, api_timeout_s=30, price_in_per_m=1.0, price_out_per_m=1.0)
MINI = ModelSpec(key="mini", gateway="opencode-zen", wire_id="mini-wire",
                 cost_model="metered", reasoning=False, omit_temp=False,
                 max_tokens=100, api_timeout_s=30, preflight_live_test=True)
GHOST = ModelSpec(key="ghost", gateway="opencode-go", wire_id="ghost-wire",
                  cost_model="subscription", reasoning=False, omit_temp=False,
                  max_tokens=100, api_timeout_s=30)

ENV = {"BAKEOFF_GATEWAY_URL": "https://x/v1", "BAKEOFF_GATEWAY_KEY": "k"}
SERVED = {"opencode-go": {"go-wire"}, "opencode-zen": {"zen-wire", "mini-wire"}}


async def fake_chat(url, headers, json, timeout):
    if json["model"] == "mini-wire":
        return 500, None, None  # live-test fails
    return 200, {"choices": [{"message": {"content": "1"}}],
                 "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                           "completion_tokens_details": {"reasoning_tokens": 0}}}, None


def test_check_gateways_flags_missing():
    issues = preflight.check_gateways([GO], {})
    assert any("opencode-go" in i for i in issues)


def test_served_ok_semantics():
    assert preflight.served_ok(GO, None) is True       # unverifiable -> allow
    assert preflight.served_ok(GO, {"go-wire"}) is True
    assert preflight.served_ok(GHOST, {"go-wire"}) is False


def test_run_all_excludes_downgrades_and_keeps_good():
    res = asyncio.run(preflight.run_all([GO, ZEN_R, MINI, GHOST], ENV, fake_chat, SERVED))
    usable = {m.key: m for m in res.usable}
    assert "go" in usable
    assert "zenR" in usable and usable["zenR"].reasoning is False  # LOW (a) downgrade
    assert "zenR" in res.reasoning_downgrades
    excluded = dict(res.excluded)
    assert "ghost" in excluded and "not served" in excluded["ghost"]  # LOW (b)
    assert "mini" in excluded and "live-test" in excluded["mini"]
    assert res.gateway_issues == []
