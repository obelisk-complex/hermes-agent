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

    @property
    def is_metered(self) -> bool:
        return self.cost_model == "metered"


@dataclass(frozen=True)
class TaskSpec:
    """One corpus task (SPEC §4): a prompt, a hidden oracle, a reference solution."""
    task_id: str
    tier: Tier
    prompt_path: str
    oracle_path: str        # hidden pytest oracle, kept outside any model-writable dir
    reference_path: str     # reference solution; must pass the oracle offline


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

    @property
    def pass_fraction(self) -> float:
        return self.n_passed / self.n_tasks if self.n_tasks else 0.0


@dataclass
class LadderResult:
    """Output of rank.assemble() (SPEC §2/§9): the report ordering and the
    weakest-first ladder for quality_gate.model_ladder."""
    report_rows: list[ModelAggregate] = field(default_factory=list)  # strongest-first
    ladder: list[str] = field(default_factory=list)                  # weakest-first model keys
    indistinguishable_pairs: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    contamination_flags: list[str] = field(default_factory=list)  # flagged task_ids (SPEC §4/§9)
