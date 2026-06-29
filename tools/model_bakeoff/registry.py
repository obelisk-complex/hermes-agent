"""The fixed roster (SPEC §3). Flags drive client behaviour; pricing for metered
(opencode-zen) models is resolved/confirmed at preflight where it is None here.

Cost model recap (SPEC §8): opencode-go and ollama-cloud are flat subscriptions the
user already pays => $0 marginal. opencode-zen is pay-as-you-go => the ONLY metered
gateway, governed by the run's hard budget cap.
"""
from __future__ import annotations

from .models import ModelSpec

ROSTER: list[ModelSpec] = [
    # --- opencode-go: subscription, $0 marginal ---
    ModelSpec(
        key="deepseek-v4-flash", gateway="opencode-go", wire_id="deepseek-v4-flash",
        cost_model="subscription", reasoning=True, omit_temp=True,
        max_tokens=16000, api_timeout_s=240,
    ),
    ModelSpec(
        key="deepseek-v4-pro", gateway="opencode-go", wire_id="deepseek-v4-pro",
        cost_model="subscription", reasoning=True, omit_temp=True,
        max_tokens=16000, api_timeout_s=240, verify_wire_id=True,
    ),
    ModelSpec(
        key="glm-5.1", gateway="opencode-go", wire_id="glm-5.1",
        cost_model="subscription", reasoning=False, omit_temp=False,
        max_tokens=8000, api_timeout_s=180,
    ),
    ModelSpec(
        key="glm-5.2", gateway="opencode-go", wire_id="glm-5.2",
        cost_model="subscription", reasoning=False, omit_temp=False,
        max_tokens=8000, api_timeout_s=180, verify_wire_id=True,
    ),
    ModelSpec(
        key="kimi-k2.6", gateway="opencode-go", wire_id="kimi-k2.6",
        cost_model="subscription", reasoning=True, omit_temp=True,
        max_tokens=16000, api_timeout_s=240, verify_wire_id=True,
    ),
    # --- ollama-cloud: Ollama Pro subscription, $0 marginal ---
    ModelSpec(
        key="qwen3.5-397b", gateway="ollama-cloud", wire_id="qwen3.5:397b",
        cost_model="subscription", reasoning=True, omit_temp=True,
        max_tokens=16000, api_timeout_s=300, verify_wire_id=True,
    ),
    # --- opencode-zen: metered (pay-as-you-go); the budget cap governs these ---
    ModelSpec(
        key="qwen3.7-max", gateway="opencode-zen", wire_id="qwen3.7-max",
        cost_model="metered", reasoning=True, omit_temp=True,
        max_tokens=16000, api_timeout_s=240, verify_wire_id=True,
    ),
    ModelSpec(
        key="minimax-m3", gateway="opencode-zen", wire_id="minimax-m3",
        cost_model="metered", reasoning=True, omit_temp=True,
        max_tokens=16000, api_timeout_s=240, verify_wire_id=True, preflight_live_test=True,
    ),
    ModelSpec(
        key="claude-opus-4-8", gateway="opencode-zen", wire_id="claude-opus-4-8",
        cost_model="metered", reasoning=False, omit_temp=False,
        max_tokens=8000, api_timeout_s=180, is_ceiling=True,
        price_in_per_m=5.0, price_out_per_m=25.0,   # Zen pricing (verified at preflight)
    ),
]


def by_key(key: str) -> ModelSpec:
    for m in ROSTER:
        if m.key == key:
            return m
    raise KeyError(f"no roster model with key {key!r}")


def metered() -> list[ModelSpec]:
    """Models that incur real API spend (opencode-zen)."""
    return [m for m in ROSTER if m.is_metered]


def reasoning_split() -> tuple[list[ModelSpec], list[ModelSpec]]:
    """(reasoning, non_reasoning) -> ranked in separate groups (SPEC §2)."""
    return ([m for m in ROSTER if m.reasoning], [m for m in ROSTER if not m.reasoning])


def ceiling() -> ModelSpec:
    """The declared ceiling, pinned last in the ladder (SPEC §2 PL1)."""
    return next(m for m in ROSTER if m.is_ceiling)
