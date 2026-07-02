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
    # reasoning_extras mirrors OpenCodeGoProfile's deepseek-v* thinking payload (shared
    # literal; do not mutate). kimi/qwen3.5 reason in-band, zen models at server default,
    # so those carry reasoning_extras=None and rely on the gateway default.
    ModelSpec(
        key="deepseek-v4-flash", gateway="opencode-go", wire_id="deepseek-v4-flash",
        cost_model="subscription", reasoning=True, omit_temp=True,
        max_tokens=16000, api_timeout_s=240,
        reasoning_extras={"thinking": {"type": "enabled"}},
        price_out_per_m=0.28,   # sticker (subscription => cost_usd still 0; drives cost_proxy only)
    ),
    ModelSpec(
        key="deepseek-v4-pro", gateway="opencode-go", wire_id="deepseek-v4-pro",
        cost_model="subscription", reasoning=True, omit_temp=True,
        max_tokens=16000, api_timeout_s=240, verify_wire_id=True,
        reasoning_extras={"thinking": {"type": "enabled"}},
        price_out_per_m=3.48,   # sticker (subscription => cost_usd still 0; drives cost_proxy only)
    ),
    ModelSpec(
        key="glm-5.1", gateway="opencode-go", wire_id="glm-5.1",
        cost_model="subscription", reasoning=False, omit_temp=False,
        max_tokens=8000, api_timeout_s=180,
    ),
    ModelSpec(
        key="glm-5.2", gateway="opencode-go", wire_id="glm-5.2",
        cost_model="subscription", reasoning=False, omit_temp=False,
        max_tokens=16000, api_timeout_s=240, verify_wire_id=True,   # A2/A11: parity with the other 3 candidates
        price_out_per_m=4.40,   # sticker (subscription => cost_usd still 0; drives cost_proxy only)
    ),
    ModelSpec(
        key="kimi-k2.6", gateway="opencode-go", wire_id="kimi-k2.6",
        cost_model="subscription", reasoning=True, omit_temp=True,
        max_tokens=16000, api_timeout_s=240, verify_wire_id=True,
    ),
    # Candidate qwen3.7-max served on the opencode-go SUBSCRIPTION endpoint (user-verified 2026-07-01).
    # Distinct roster KEY from the metered zen qwen3.7-max; same wire_id, but $0 marginal here.
    ModelSpec(
        key="qwen3.7-max-go", gateway="opencode-go", wire_id="qwen3.7-max",
        cost_model="subscription", reasoning=True, omit_temp=True,
        max_tokens=16000, api_timeout_s=240, verify_wire_id=True,
        price_out_per_m=3.75,   # sticker approx; CONFIRM AT PREFLIGHT (Task 10 Step 2)
    ),
    # --- ollama-cloud: Ollama Pro subscription, $0 marginal ---
    ModelSpec(
        key="qwen3.5-397b", gateway="ollama-cloud", wire_id="qwen3.5:397b",
        cost_model="subscription", reasoning=True, omit_temp=True,
        max_tokens=16000, api_timeout_s=300, verify_wire_id=True,
    ),
    # qwen coder open-weights served on Ollama Pro. Substitute qwen slot: qwen3.7-max/-plus are
    # Alibaba closed-API tiers reachable only via opencode-go, which was 503 across the whole 3.7
    # tier (2026-07-02), and are not hosted on Ollama. These two are NON-reasoning (emit code
    # directly, no reasoning field). No price: subscription open-weight, so cost_proxy stays 0
    # (parity with qwen3.5-397b). preflight_live_test guards against Ollama Cloud correlated outages.
    ModelSpec(
        key="qwen3-coder-480b", gateway="ollama-cloud", wire_id="qwen3-coder:480b",
        cost_model="subscription", reasoning=False, omit_temp=False,
        max_tokens=16000, api_timeout_s=300, verify_wire_id=True, preflight_live_test=True,
    ),
    ModelSpec(
        key="qwen3-coder-next", gateway="ollama-cloud", wire_id="qwen3-coder-next",
        cost_model="subscription", reasoning=False, omit_temp=False,
        max_tokens=16000, api_timeout_s=300, verify_wire_id=True, preflight_live_test=True,
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


# The elegance judge (coding bakeoff). Deliberately NOT in ROSTER: it never competes and must not be
# selected as a candidate. It is cross-family (Anthropic) vs the DeepSeek/Zhipu/Alibaba candidates, so
# it cannot self-grade (judge.judge_conflicts enforces this at run time).
# NOTE: verify_wire_id is INERT here -- preflight.run_all only iterates ROSTER via _select, and the judge
# is not in ROSTER, so it is never preflighted through that path. The judge's wire_id is verified instead
# by the standalone live-test in Task 10 Step 3 (a direct client.call against judge_spec()).
_JUDGE = ModelSpec(
    key="claude-sonnet", gateway="opencode-zen", wire_id="claude-sonnet-4-6",
    cost_model="metered", reasoning=False, omit_temp=False,
    max_tokens=2000, api_timeout_s=180, verify_wire_id=True,
    price_in_per_m=3.0, price_out_per_m=15.0,   # sticker; CONFIRM AT PREFLIGHT (Task 10 Step 3)
)


def judge_spec() -> ModelSpec:
    """The elegance judge (NOT a roster candidate). Callers may override key/wire_id via --judge."""
    return _JUDGE


def metered() -> list[ModelSpec]:
    """Models that incur real API spend (opencode-zen)."""
    return [m for m in ROSTER if m.is_metered]


def reasoning_split() -> tuple[list[ModelSpec], list[ModelSpec]]:
    """(reasoning, non_reasoning) -> ranked in separate groups (SPEC §2)."""
    return ([m for m in ROSTER if m.reasoning], [m for m in ROSTER if not m.reasoning])


def ceiling() -> ModelSpec:
    """The declared ceiling, pinned last in the ladder (SPEC §2 PL1)."""
    return next(m for m in ROSTER if m.is_ceiling)
