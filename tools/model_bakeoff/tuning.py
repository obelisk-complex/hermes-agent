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
import os
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


def _default_seed(spec: ModelSpec) -> SettingsProfile:
    return SettingsProfile(max_tokens=spec.max_tokens, api_timeout_s=spec.api_timeout_s)


def _default_grid(spec: ModelSpec) -> dict:
    """Every model (incl. omit_temp reasoning models) gets max_tokens + api_timeout_s neighbours;
    non-omit models also get sampling. Gateway is searched ONLY when the caller supplies
    extra_gateways (metered gateways are pruned)."""
    grid = {
        "max_tokens": sorted({spec.max_tokens, min(spec.max_tokens * 2, 48000),
                              min(spec.max_tokens * 3, 48000)}),
        "api_timeout_s": sorted({spec.api_timeout_s, min(spec.api_timeout_s * 2, 600)}),
    }
    if not spec.omit_temp:
        grid["temperature"] = [0.2, 0.7]   # seed (temperature None -> 0) already covers effective 0
        grid["top_p"] = [0.9]
    return grid


def _prune_gateways(grid: dict, conn_for, notes: list) -> dict:
    """Drop metered (never tune with real spend) and unconfigured gateways from the grid, loudly."""
    if "gateway" not in grid:
        return grid
    kept = []
    for gw, wid in grid["gateway"]:
        if gw in METERED_GATEWAYS:
            notes.append(f"note: dropped metered gateway {gw} from the search (subscription-only)")
            continue
        try:
            conn = conn_for(gw)
        except KeyError:
            notes.append(f"note: dropped unknown gateway {gw} from the search")
            continue
        if not getattr(conn, "ok", False):
            notes.append(f"note: dropped unconfigured gateway {gw} from the search")
            continue
        kept.append((gw, wid))
    g = dict(grid)
    if kept:
        g["gateway"] = kept
    else:
        g.pop("gateway", None)
    return g


def _persist_candidate(raw_dir: str, ev: ProfileEval) -> None:
    """Write each of a candidate's TaskRuns to <raw_dir>/<sig_hash>__<task>__r<idx>.json. This is an
    INDEPENDENT reimplementation of cli._persist_raw's JSON shape; it never imports cli (that would
    create a cli -> tuning -> cli cycle)."""
    os.makedirs(raw_dir, exist_ok=True)
    sig = _sig_hash(ev.profile)
    for i, tr in enumerate(ev.runs):
        path = os.path.join(raw_dir, f"{sig}__{tr.call.task_id}__r{i}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"sig": sig, "task": tr.call.task_id, "passed": tr.score.passed,
                       "error_type": tr.score.error_type, "latency_s": tr.call.total_latency_s,
                       "completion_tokens": tr.call.completion_tokens,
                       "thinking_tokens": tr.call.thinking_tokens,
                       "raw_response": tr.call.raw_response,
                       "extracted_code": tr.call.extracted_code}, f, indent=2)


def _metrics(ev: ProfileEval) -> dict:
    return {"pass_fraction": ev.pass_fraction, "mean_output_tokens": ev.mean_output_tokens,
            "p50_latency_s": ev.p50_latency_s, "n_operational": ev.n_operational, "n_runs": ev.n_runs}


def _record(spec, seed, best_profile, best_ev, trace) -> dict:
    """The persisted best_settings.json (schema_version 1). `base` is a full ModelSpec snapshot so
    sub-project D can reconstruct the exact tuned spec:
    apply_profile(ModelSpec(**base), SettingsProfile(**{k:v for k,v in winner.items() if v is not None}))."""
    seed_ev = trace[0][1]
    margin = best_ev.n_passed - seed_ev.n_passed
    all_op = [t for (_, t) in trace if t.n_runs > 0 and t.n_operational == t.n_runs]
    reasons, caveats = [], []
    if len(trace) - 1 == 0:
        reasons.append("no_search_performed")
        caveats.append("no valid neighbours were searched; winner is the seed")
    if margin <= 1 and best_ev.n_runs <= 6:
        reasons.append("small_margin")
        caveats.append(f"winner beats seed by {margin}/{best_ev.n_runs} passed runs; may be within noise")
    if all_op:
        reasons.append("all_operational")
        caveats.append(f"{len(all_op)} candidate(s) had ALL calls fail at the gateway (503 / possible "
                       "invalid parameter); check gateway health before trusting this record")
    return {
        "schema_version": 1, "model_key": spec.key,
        "base": dataclasses.asdict(spec), "seed": dataclasses.asdict(seed),
        "winner": dataclasses.asdict(best_profile), "achieved": _metrics(best_ev),
        "winner_margin_passed": margin, "neighbours_evaluated": len(trace) - 1,
        "search": "coordinate-ascent, single seed; local optimum, not guaranteed global",
        "low_confidence": bool(reasons), "reasons": reasons, "caveats": caveats,
        "trace": [{"profile": dataclasses.asdict(p), **_metrics(t), "sample_error": t.sample_error}
                  for (p, t) in trace],
    }


def _write_record(out_dir: str, record: dict) -> None:
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "best_settings.json"), "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)


async def tune_model(spec, tasks, conn_for, transport, out_dir, seed=None, grid=None,
                     extra_gateways=None, repeats=2, sandbox_timeout=60) -> dict:
    """Hill-climb `spec`'s settings on `tasks`; persist per-candidate raw runs and rewrite
    best_settings.json after EVERY candidate (crash loses at most one candidate). Returns the record.
    Subscription-only: metered/unconfigured gateways are pruned from the grid before the climb."""
    os.makedirs(out_dir, exist_ok=True)
    raw_dir = os.path.join(out_dir, "raw")
    notes: list = []
    seed = seed or _default_seed(spec)
    grid = grid or _default_grid(spec)
    if extra_gateways:
        grid = dict(grid)
        grid["gateway"] = [(spec.gateway, spec.wire_id)] + list(extra_gateways)
    grid = _prune_gateways(grid, conn_for, notes)
    best = {"profile": None, "ev": None}

    async def _evaluate(profile):
        return await evaluate_profile(spec, profile, tasks, conn_for, transport, repeats, sandbox_timeout)

    def _on(cand, ev, trace):
        _persist_candidate(raw_dir, ev)
        if best["ev"] is None or profile_score_key(ev) > profile_score_key(best["ev"]):
            best["profile"], best["ev"] = cand, ev
        _write_record(out_dir, _record(spec, seed, best["profile"], best["ev"], trace))

    best_profile, best_ev, trace = await hill_climb(spec, seed, grid, _evaluate, on_candidate=_on)
    rec = _record(spec, seed, best_profile, best_ev, trace)
    rec["notes"] = notes
    _write_record(out_dir, rec)
    return rec
