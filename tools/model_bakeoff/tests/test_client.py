"""client request-building, response-parsing, cost (SPEC §5). Offline, no network."""
from __future__ import annotations

import asyncio

from tools.model_bakeoff import client
from tools.model_bakeoff.models import ModelSpec

SUB = ModelSpec(key="sub", gateway="opencode-go", wire_id="deepseek-v4-flash",
                cost_model="subscription", reasoning=True, omit_temp=True,
                max_tokens=16000, api_timeout_s=240)
ZEN = ModelSpec(key="opus", gateway="opencode-zen", wire_id="claude-opus-4-8",
                cost_model="metered", reasoning=False, omit_temp=False,
                max_tokens=8000, api_timeout_s=180, is_ceiling=True,
                price_in_per_m=5.0, price_out_per_m=25.0)


def test_payload_omits_temperature_when_flagged():
    p = client.build_payload(SUB, "do x", "NONCE")
    assert "temperature" not in p
    assert p["model"] == "deepseek-v4-flash"
    assert p["max_tokens"] == 16000
    assert "NONCE" in p["messages"][0]["content"]


def test_payload_sends_zero_temperature_when_not_omitted():
    p = client.build_payload(ZEN, "do x", "N")
    assert p["temperature"] == 0


def test_headers_carry_hermes_ua_and_no_store():
    h = client.build_headers("KEY123")
    assert h["User-Agent"] == client.UA == "hermes-cli/0.17.0"
    assert h["Authorization"] == "Bearer KEY123"
    assert h["Cache-Control"] == "no-store"


def test_nonce_changes_each_call():
    assert client.make_nonce() != client.make_nonce()


def test_usage_extracts_reasoning_tokens():
    resp = {"choices": [{"message": {"content": "hi"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50,
                      "completion_tokens_details": {"reasoning_tokens": 200}}}
    r = client.parse_response(SUB, "t", 200, resp, 0.3, 1.2)
    assert (r.prompt_tokens, r.completion_tokens, r.thinking_tokens) == (100, 50, 200)
    assert r.raw_response == "hi"


def test_cost_subscription_is_zero():
    assert client.cost_usd(SUB, 1000, 1000, 1000) == 0.0


def test_cost_metered_bills_thinking_as_output():
    # in: 1000 * $5/M = $0.005 ; out: (1000 completion + 1000 thinking) * $25/M = $0.05
    c = client.cost_usd(ZEN, 1000, 1000, 1000)
    assert abs(c - 0.055) < 1e-9


def test_cache_hit_flagged_below_threshold():
    resp = {"choices": [{"message": {"content": "x"}}], "usage": {}}
    fast = client.parse_response(SUB, "t", 200, resp, 0.01, 0.02)
    slow = client.parse_response(SUB, "t", 200, resp, 0.3, 1.5)
    assert fast.cache_hit and not slow.cache_hit


def test_non_200_is_not_ok():
    r = client.parse_response(SUB, "t", 403, "forbidden", None, 0.4)
    assert not r.ok and "403" in r.error


def test_call_uses_injected_transport_with_correct_request():
    captured = {}

    async def fake(url, headers, json, timeout):
        captured.update(url=url, headers=headers, json=json, timeout=timeout)
        return 200, {"choices": [{"message": {"content": "ok"}}],
                     "usage": {"prompt_tokens": 7, "completion_tokens": 3}}, 0.2

    r = asyncio.run(client.call(SUB, "t1", "solve", "KEY", "https://gw/v1", fake,
                                retry_on_cache_hit=False))
    assert r.ok and r.prompt_tokens == 7
    assert captured["url"] == "https://gw/v1/chat/completions"
    assert captured["headers"]["User-Agent"] == client.UA
    assert captured["json"]["model"] == "deepseek-v4-flash"
    assert captured["timeout"] == 240


def test_transport_exception_is_surfaced_not_swallowed():
    async def boom(url, headers, json, timeout):
        raise ConnectionError("refused")

    r = asyncio.run(client.call(SUB, "t1", "solve", "KEY", "https://gw/v1", boom,
                                retry_on_cache_hit=False))
    assert not r.ok and "ConnectionError" in r.error


def test_payload_merges_reasoning_extras_when_present():
    extras = {"thinking": {"type": "enabled"}}
    p = client.build_payload(SUB, "do x", "N", reasoning_extras=extras)
    assert p["thinking"] == {"type": "enabled"}


def test_payload_has_no_thinking_key_without_reasoning_extras():
    p = client.build_payload(SUB, "do x", "N")
    assert "thinking" not in p


def test_payload_does_not_alias_reasoning_extras_inner_dict():
    # M2: a future per-call mutation of the payload must not corrupt the shared registry literal.
    extras = {"thinking": {"type": "enabled"}}
    p = client.build_payload(SUB, "x", "n", reasoning_extras=extras)
    p["thinking"]["mutated"] = True
    assert extras == {"thinking": {"type": "enabled"}}  # source dict untouched (deep copy)
