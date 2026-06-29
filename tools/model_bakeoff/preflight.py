"""Live preflight (SPEC §10). Folds the two audited LOWs:
(a) probe reasoning_tokens for metered reasoning models; downgrade to
    non-reasoning if the gateway reports zero (so ranking groups them honestly).
(b) assert each model's wire_id is actually served (/v1/models); loud-exclude on
    miss. A gateway that cannot be listed (None) is NOT excluded, only noted.
Plus minimax-m3's preflight_live_test. The chat transport and the served-id map
are injected, so the whole thing is offline-testable with no network.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

from . import client, gateways
from .models import ModelSpec

_PROBE = "Reply with only the single digit 1."


@dataclass
class PreflightResult:
    usable: list  # ModelSpec, reasoning possibly downgraded
    excluded: list  # (key, reason)
    gateway_issues: list  # human-readable strings
    reasoning_downgrades: list  # keys downgraded to non-reasoning
    notes: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.gateway_issues and bool(self.usable)


def check_gateways(models, env) -> list[str]:
    issues = []
    for gw in sorted({m.gateway for m in models}):
        conn = gateways.resolve(gw, env)
        if not conn.ok:
            issues.append(f"{gw}: missing {', '.join(conn.missing)}")
    return issues


def served_ok(spec: ModelSpec, served_ids) -> bool:
    if served_ids is None:  # could not list -> do not exclude, just proceed
        return True
    return spec.wire_id in served_ids


async def probe_reasoning(spec, base_url, key, transport):
    r = await client.call(spec, "_probe", _PROBE, key, base_url, transport, retry_on_cache_hit=False)
    if not r.ok:
        return None, f"reasoning probe failed: {r.error}"
    return (r.thinking_tokens > 0), f"reasoning_tokens={r.thinking_tokens}"


async def live_test(spec, base_url, key, transport):
    r = await client.call(spec, "_livetest", _PROBE, key, base_url, transport, retry_on_cache_hit=False)
    return (r.ok and bool((r.raw_response or "").strip())), (r.error or "empty response")


async def run_all(models, env, chat_transport, served_by_gateway=None) -> PreflightResult:
    served_by_gateway = served_by_gateway or {}
    gateway_issues = check_gateways(models, env)
    blocked = {gw for gw in {m.gateway for m in models} if not gateways.resolve(gw, env).ok}

    usable, excluded, downgrades, notes = [], [], [], []
    for spec in models:
        if spec.gateway in blocked:
            excluded.append((spec.key, f"gateway {spec.gateway} unconfigured"))
            continue
        conn = gateways.resolve(spec.gateway, env)
        if not served_ok(spec, served_by_gateway.get(spec.gateway)):
            excluded.append((spec.key, f"wire_id '{spec.wire_id}' not served by {spec.gateway}"))
            continue

        eff = spec
        if spec.is_metered and spec.reasoning:  # LOW (a)
            has, detail = await probe_reasoning(spec, conn.base_url, conn.api_key, chat_transport)
            notes.append(f"{spec.key}: {detail}")
            if has is False:
                eff = dataclasses.replace(spec, reasoning=False)
                downgrades.append(spec.key)

        if spec.preflight_live_test:  # minimax-m3 etc.
            ok, detail = await live_test(spec, conn.base_url, conn.api_key, chat_transport)
            if not ok:
                excluded.append((spec.key, f"live-test failed: {detail}"))
                continue

        usable.append(eff)
    return PreflightResult(usable, excluded, gateway_issues, downgrades, notes)
