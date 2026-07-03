"""Shared data contracts for the model bakeoff. Pure data, no I/O, no API calls.

Every other module speaks in terms of these dataclasses so the leaf modules
(extractor, sandbox, scorer, rank, client) can be built and tested in isolation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

Gateway = Literal["opencode-go", "opencode-zen", "ollama-cloud"]
CostModel = Literal["subscription", "metered"]
Tier = Literal["quick", "standard", "thorough"]

# error_type values produced by the scorer (SPEC §5)
ERR_EXTRACTION = "extraction_failed"   # no code could be extracted from the response
ERR_COLLECTION = "collection_error"    # import/syntax error: pytest could not collect
ERR_TEST_FAIL = "test_failure"         # code ran, oracle assertions failed
ERR_TIMEOUT = "timeout"                # hard wall-clock timeout in the sandbox
ERR_CALL = "call_error"                # the API call itself failed (non-200 / transport error)
ERR_OUTPUT_CAP = "output_cap_exceeded"  # sandbox output exceeded the per-stream size cap (SPEC §6)

# Operational (provider/gateway) failures: the gateway never returned a scoreable completion, so the
# model's answer was never evaluated. This is the ONLY reliability-axis bucket (sub-project B) and the
# per-gateway failure numerator. Every other error_type means we DID get a completion / the code DID
# run, so it is model- or settings-attributable (a wrong answer, non-compiling code, a hung sandbox,
# runaway output, or a truncation whose token budget sub-project C tunes) - never counted here.
OPERATIONAL_ERROR_TYPES = frozenset({ERR_CALL})


@dataclass(frozen=True)
class ModelSpec:
    """One roster entry (SPEC §3). Flags are explicit so the client never guesses."""
    key: str                       # unique display key, e.g. "deepseek-v4-flash"
    gateway: Gateway
    wire_id: str                   # provider wire id sent on the API call
    cost_model: CostModel
    reasoning: bool
    omit_temp: bool                # sole source of truth for temperature (SPEC §3 PM1)
    max_tokens: int
    api_timeout_s: int
    is_ceiling: bool = False       # claude-opus-4-8: pinned last in the ladder (SPEC §2 PL1)
    # metered pricing, USD per 1M tokens; None => resolve at preflight/estimate.
    price_in_per_m: Optional[float] = None
    price_out_per_m: Optional[float] = None
    verify_wire_id: bool = False   # preflight must confirm wire_id appears in /v1/models
    preflight_live_test: bool = False  # send a minimal completion at preflight (SPEC §3 PM4)
    # Reasoning controls merged verbatim into the request body (SPEC §3 PM1). Mirrors
    # OpenCodeGoProfile.build_api_kwargs_extras(model=...) for reasoning_config=None; mirrored
    # not imported because the hyphenated plugin path is not importable and the bakeoff stays
    # decoupled/offline-testable. SHARED LITERAL: never mutate in place; build_payload DEEP-copies
    # it into the payload (a shallow copy would leave the inner dict aliased to this registry entry).
    reasoning_extras: Optional[dict] = None
    # Sampling knobs (sub-project C). None => inherit today's behaviour (temperature 0, no top_p/top_k).
    # Only ever sent when omit_temp is False; reasoning models that reject sampling keep omit_temp=True.
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    top_k: Optional[int] = None

    @property
    def is_metered(self) -> bool:
        return self.cost_model == "metered"


@dataclass(frozen=True)
class SettingsProfile:
    """A settings override layered over a ModelSpec (sub-project C). Every field is optional;
    None means "inherit the spec default". apply_profile() (tuning.py) validates + applies it via
    dataclasses.replace, so the roster is never edited. Note (None-sentinel limitation): None cannot
    express "no reasoning_extras" (it means inherit); to DISABLE reasoning pass an explicit
    gateway-specific dict, not None."""
    max_tokens: Optional[int] = None
    api_timeout_s: Optional[int] = None
    omit_temp: Optional[bool] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    reasoning_extras: Optional[dict] = None
    gateway: Optional[Gateway] = None
    wire_id: Optional[str] = None


@dataclass(frozen=True)
class TaskSpec:
    """One corpus task (SPEC §4): a prompt, a hidden oracle, a reference solution."""
    task_id: str
    tier: Tier
    prompt_path: str
    oracle_path: str        # hidden pytest oracle, kept outside any model-writable dir
    reference_path: str     # reference solution; must pass the oracle offline
    tags: tuple[str, ...] = ()   # from optional meta.yaml; drives suite tag-selection (frozen -> tuple)


@dataclass
class ExtractionResult:
    """Output of extractor.extract() (SPEC §5)."""
    code: str
    failed: bool = False
    method: str = ""        # which strategy matched: "fenced-python" | "fenced-any" | "whole" | "none"


@dataclass
class SandboxResult:
    """Raw result of running extracted code against an oracle in an isolated
    subprocess (SPEC §5/§6). The scorer interprets this; the sandbox does not judge."""
    returncode: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    truncated: bool = False    # output hit the per-stream size cap; verdict unreliable (SPEC §6)
    duration_s: float = 0.0


@dataclass
class ScoreResult:
    """Scorer verdict for one (model, task) (SPEC §5)."""
    model_key: str
    task_id: str
    passed: bool
    error_type: Optional[str] = None   # one of the ERR_* constants, or None on pass
    detail: str = ""


@dataclass
class CallResult:
    """One model call on one task (SPEC §5/§9). Persisted per (model, task)."""
    model_key: str
    task_id: str
    ok: bool
    raw_response: str = ""
    extracted_code: str = ""
    extraction_failed: bool = False
    ttft_s: Optional[float] = None
    total_latency_s: Optional[float] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    thinking_tokens: int = 0
    cache_hit: bool = False           # latency < 100ms => suspected cache hit (SPEC §5)
    cost_usd: float = 0.0
    error: str = ""


@dataclass
class ModelAggregate:
    """Per-model rollup over the corpus, consumed by rank.py (SPEC §2)."""
    model_key: str
    reasoning: bool
    cost_model: CostModel
    n_tasks: int
    n_passed: int
    cost_per_task_usd: float = 0.0     # 0.0 for subscription ($0 marginal)
    p50_latency_s: Optional[float] = None
    # Wilson 95% CI on pass_fraction (SPEC §8/§9)
    ci_low: float = 0.0
    ci_high: float = 0.0
    # Coding-bakeoff additive report-only fields (do NOT affect the pass_fraction ladder).
    mean_elegance: Optional[float] = None       # mean LLM-judge elegance over judged cells, None if none judged
    cost_proxy_per_task_usd: float = 0.0        # sticker-price x output-token proxy (subscription cost_usd stays 0)
    n_elegance_judged: int = 0                  # how many cells contributed to mean_elegance
    n_latency_samples: int = 0                  # cache-hit-clean latency samples behind p50 (A8 disambiguation)
    # Reliability axis (sub-project B). Report-only; does NOT affect pass_fraction or the ladder.
    gateway: Optional[Gateway] = None           # which gateway served this model (for per-gateway rollup)
    error_counts: dict = field(default_factory=dict)   # {error_type: count} over this model's failed runs
    n_operational: int = 0                      # runs whose error_type is operational (provider) - not wrong answers

    @property
    def pass_fraction(self) -> float:
        return self.n_passed / self.n_tasks if self.n_tasks else 0.0

    @property
    def completed_pass_fraction(self) -> Optional[float]:
        """Pass fraction over cells that actually COMPLETED (operational/provider failures excluded).
        None when every attempt was operational - so an all-503 model is never a fake 0%."""
        denom = self.n_tasks - self.n_operational
        return (self.n_passed / denom) if denom > 0 else None


@dataclass(frozen=True)
class TaskMetric:
    """Per-task outcome for ONE phase of a dual run (sub-project D), reconstructed from the persisted
    raw by cli._phase_metrics. A task appears here ONLY IF it completed in exactly `repeats` files with
    no operational failure; an operational or partial task is OMITTED entirely (never a False), so the
    paired significance test's presence-based pairing cannot be contaminated. passed = all repeats
    passed; pass_rate = passed_repeats/repeats (the D6g flakiness signal); latency_s = median of the
    cache-hit-clean repeat latencies (None if all were excluded); cost_proxy_usd = mean per-repeat
    sticker-price output proxy; elegance = mean judged elegance (None if none judged)."""
    passed: bool
    latency_s: Optional[float]
    cost_proxy_usd: float
    elegance: Optional[float]
    pass_rate: float


@dataclass
class LadderResult:
    """Output of rank.assemble() (SPEC §2/§9): the report ordering and the
    weakest-first ladder for quality_gate.model_ladder."""
    report_rows: list[ModelAggregate] = field(default_factory=list)  # strongest-first
    ladder: list[str] = field(default_factory=list)                  # weakest-first model keys
    indistinguishable_pairs: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    contamination_flags: list[str] = field(default_factory=list)  # flagged task_ids (SPEC §4/§9)
    # Per-gateway reliability (sub-project B): {gateway: {attempts, operational, failure_rate}}.
    gateway_reliability: dict = field(default_factory=dict)
