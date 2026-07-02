"""Sub-project C: per-model best-shot settings tuning.

A SettingsProfile is layered over a ModelSpec via apply_profile (no roster edits); a
coordinate-ascent hill-climb searches the profile space on a held-out dev set (objective = oracle
pass-rate). The search is SINGLE-SEED coordinate ascent: it finds a LOCAL optimum, is
order-dependent, and on a flat objective the winner among ties is decided only by the token/latency
tie-break. Offline-testable: the transport AND the gateway connection RESOLVER are injected, exactly
as runner is. Tuning is subscription-only: metered gateways are refused (there is no budget cap
here).
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import statistics
from typing import Optional

from . import runner
from .models import ModelSpec, OPERATIONAL_ERROR_TYPES, SettingsProfile

# Per registry.py: opencode-zen is the ONLY pay-as-you-go gateway. Tuning must never issue a live
# metered call, so any candidate whose (tuned) gateway is metered is pruned/refused.
METERED_GATEWAYS = frozenset({"opencode-zen"})


def apply_profile(spec: ModelSpec, profile: SettingsProfile) -> ModelSpec:
    """Return a NEW ModelSpec with the profile's non-None fields overlaid (spec is never mutated).
    Raises ValueError on an incoherent override: a gateway change without a matching wire_id, or a
    sampling knob set on an omit_temp model (reasoning models that omit temperature reject them)."""
    overrides = {f.name: getattr(profile, f.name)
                 for f in dataclasses.fields(profile) if getattr(profile, f.name) is not None}
    if "gateway" in overrides and "wire_id" not in overrides:
        raise ValueError(f"profile overrides gateway to {overrides['gateway']!r} without a wire_id; "
                         "the same model has a different wire id per gateway, so both are required")
    new = dataclasses.replace(spec, **overrides)
    if new.omit_temp and any(v is not None for v in (new.temperature, new.top_p, new.top_k)):
        raise ValueError(f"model {new.key} has omit_temp=True but the profile sets sampling knobs "
                         "(temperature/top_p/top_k); reasoning models that omit temperature reject them")
    return new


@dataclasses.dataclass
class ProfileEval:
    """One profile's result over the dev tasks (repeats folded together). mean_output_tokens is over
    OK runs only and is None when there were no ok runs (so a fully call-errored candidate is never
    reported as "0 tokens => most efficient"). runs are kept for durable persistence."""
    profile: SettingsProfile
    spec: ModelSpec
    n_runs: int
    n_passed: int
    n_operational: int
    mean_output_tokens: Optional[float]
    p50_latency_s: Optional[float]
    sample_error: str
    runs: list

    @property
    def pass_fraction(self) -> float:
        return self.n_passed / self.n_runs if self.n_runs else 0.0


async def evaluate_profile(spec, profile, tasks, conn_for, transport, repeats=2, sandbox_timeout=60):
    """Apply the profile, resolve the TUNED gateway's own connection (conn_for is injected), and run
    `repeats` passes of the dev tasks. Raises loudly if the tuned gateway is metered (tuning is
    subscription-only) or unconfigured. Metrics are computed from the flat run list, not
    runner.aggregate (which zeroes the subscription token proxy)."""
    tuned = apply_profile(spec, profile)
    if tuned.gateway in METERED_GATEWAYS:
        raise ValueError(f"refusing to tune {tuned.key} on metered gateway {tuned.gateway!r}; "
                         "tuning is subscription-only and has no budget cap")
    conn = conn_for(tuned.gateway)
    if not getattr(conn, "ok", False):
        raise ValueError(f"gateway {tuned.gateway!r} for tuned {tuned.key} is unconfigured "
                         "(missing base_url/api_key); cannot evaluate this candidate")
    all_runs = []
    for _ in range(repeats):
        runs, _warmups = await runner.run_model(tuned, tasks, conn.api_key, conn.base_url, transport,
                                                sandbox_timeout=sandbox_timeout)
        all_runs += runs
    n = len(all_runs)
    n_pass = sum(1 for r in all_runs if r.score.passed)
    n_op = sum(1 for r in all_runs if r.score.error_type in OPERATIONAL_ERROR_TYPES)
    ok_toks = [r.call.completion_tokens + r.call.thinking_tokens for r in all_runs if r.call.ok]
    lat = [r.call.total_latency_s for r in all_runs
           if r.call.total_latency_s is not None and not r.call.cache_hit]
    return ProfileEval(
        profile=profile, spec=tuned, n_runs=n, n_passed=n_pass, n_operational=n_op,
        mean_output_tokens=(statistics.mean(ok_toks) if ok_toks else None),
        p50_latency_s=(statistics.median(lat) if lat else None),
        # only FAILED calls' errors are a diagnostic; a successful call may carry a benign
        # "retried-after-suspected-cache-hit" annotation that must not read as a failure sample.
        sample_error=next((r.call.error for r in all_runs if not r.call.ok and r.call.error), ""),
        runs=all_runs)


def profile_score_key(ev: ProfileEval) -> tuple:
    """Objective, MAXIMISED: higher pass_fraction, then fewer output tokens, faster p50, fewer
    operational failures. mean_output_tokens and p50 are guarded None -> +inf, so a candidate with no
    measurable successful output sorts WORST on those tie-breaks (never "most efficient"); a
    zero-pass candidate already loses on the leading pass_fraction term."""
    toks = ev.mean_output_tokens if ev.mean_output_tokens is not None else float("inf")
    p50 = ev.p50_latency_s if ev.p50_latency_s is not None else float("inf")
    return (ev.pass_fraction, -toks, -p50, -ev.n_operational)


def _signature(p: SettingsProfile) -> tuple:
    """A hashable identity for a profile: sorted (name, value) of its non-None fields; dict values
    are JSON-serialised so two equal reasoning_extras dicts collapse to the same signature."""
    items = []
    for f in dataclasses.fields(p):
        v = getattr(p, f.name)
        if v is not None:
            items.append((f.name, json.dumps(v, sort_keys=True) if isinstance(v, dict) else v))
    return tuple(sorted(items, key=lambda kv: kv[0]))


def _sig_hash(p: SettingsProfile) -> str:
    return hashlib.sha1(json.dumps([list(x) for x in _signature(p)], sort_keys=True).encode()).hexdigest()[:12]


def _neighbours(spec: ModelSpec, current: SettingsProfile, grid: dict) -> list:
    """One-knob neighbours of `current` from `grid` ({field: [values]}). The "gateway" key is a
    PAIRED knob: its values are (gateway, wire_id) tuples set together in one replace, so the result
    always satisfies apply_profile's gateway+wire_id rule. Candidates identical to `current`, or that
    apply_profile rejects (e.g. sampling under omit_temp), are skipped, so a uniform grid self-prunes
    per model."""
    out = []
    for field, values in grid.items():
        for v in values:
            if field == "gateway":
                gw, wid = v
                cand = dataclasses.replace(current, gateway=gw, wire_id=wid)
            else:
                cand = dataclasses.replace(current, **{field: v})
            if _signature(cand) == _signature(current):
                continue
            try:
                apply_profile(spec, cand)   # validity gate; skip invalid combos for this model
            except ValueError:
                continue
            out.append(cand)
    return out


async def hill_climb(spec, seed, grid, evaluate, max_rounds=8, on_candidate=None):
    """Greedy coordinate ascent from `seed`. Each round evaluates every unseen one-knob neighbour and
    moves to the best strictly-improving one; stops when a round yields no improvement or max_rounds
    is hit (fail-safe against a non-terminating climb). on_candidate(cand, ev, trace) fires after each
    evaluation (used for incremental persistence). Returns (best_profile, best_eval, trace)."""
    current = seed
    best_ev = await evaluate(current)
    trace = [(current, best_ev)]
    if on_candidate:
        on_candidate(current, best_ev, trace)
    seen = {_signature(current)}
    for _ in range(max_rounds):
        improved = False
        round_best, round_best_ev = current, best_ev
        for cand in _neighbours(spec, current, grid):
            sig = _signature(cand)
            if sig in seen:
                continue
            seen.add(sig)
            ev = await evaluate(cand)
            trace.append((cand, ev))
            if on_candidate:
                on_candidate(cand, ev, trace)
            if profile_score_key(ev) > profile_score_key(round_best_ev):
                round_best, round_best_ev, improved = cand, ev, True
        if not improved:
            break
        current, best_ev = round_best, round_best_ev
    return current, best_ev, trace
