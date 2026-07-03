"""Command surface for the bakeoff (SPEC §10).

  validate-oracles  offline: every reference solution must pass its oracle
  estimate          offline: projected tokens + metered $ for a model set
  preflight         live: resolve gateways, assert wire-ids, reasoning probe, live-test
  run               live: run the roster over the corpus, persist all artefacts

run/preflight default to the live httpx transports but accept injected ones, so
the orchestration is exercised offline end-to-end. Raw model outputs are persisted
(generated products are kept, never discarded).
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import re
import statistics
import sys
from datetime import datetime, timezone

from . import (
    client, corpus, gateways, judge as judge_mod, preflight as preflight_mod,
    rank, registry, report, runner, settings_loader, tuning)
from .http_transport import http_transport, list_models
from .models import OPERATIONAL_ERROR_TYPES, ModelSpec, TaskMetric

THINKING_BUDGET_MULTIPLIER = 4.0  # reasoning models emit ~4x output tokens (SPEC §8)
DEFAULT_OUT_TOKENS = 500


def _select(models_arg):
    roster = registry.ROSTER
    if models_arg:
        wanted = {k.strip() for k in models_arg.split(",") if k.strip()}
        roster = [m for m in roster if m.key in wanted]
    return roster


def _approx_prompt_tokens(task) -> int:
    with open(task.prompt_path, "r", encoding="utf-8") as f:
        return max(1, len(f.read()) // 4) + 40  # ~4 chars/token + chat overhead


# ---- estimate -------------------------------------------------------------

def estimate(models, tasks, repeats):
    rows, total = [], 0.0
    in_per_run = sum(_approx_prompt_tokens(t) for t in tasks)
    unpriced = []  # metered models with no price => real spend is NOT in `total`
    for m in models:
        in_t = in_per_run * repeats
        out_t = DEFAULT_OUT_TOKENS * len(tasks) * repeats
        think_t = int(out_t * (THINKING_BUDGET_MULTIPLIER - 1)) if m.reasoning else 0
        cost = 0.0
        priced = bool(m.price_in_per_m and m.price_out_per_m)
        if m.is_metered and priced:
            cost = (in_t * m.price_in_per_m + (out_t + think_t) * m.price_out_per_m) / 1_000_000.0
        elif m.is_metered and not priced:
            unpriced.append(m.key)
        total += cost
        rows.append((m.key, in_t, out_t + think_t, cost, m.is_metered and not priced))
    return rows, total, unpriced


def _project_judge_spend(tasks, repeats):
    """Worst-case (all cells pass) projected elegance-judge spend, in USD. The judge is metered and
    counts against --budget, but is NOT in `estimate()`'s candidate total (A4)."""
    jspec = registry.judge_spec()
    if not (jspec.price_in_per_m and jspec.price_out_per_m):
        return 0.0
    n_cells = len(tasks) * repeats
    in_per_task = (sum(_approx_prompt_tokens(t) for t in tasks) / len(tasks)) if tasks else 0
    judge_in = in_per_task + DEFAULT_OUT_TOKENS   # task prompt + the solution being judged
    judge_out = DEFAULT_OUT_TOKENS
    return n_cells * (judge_in * jspec.price_in_per_m + judge_out * jspec.price_out_per_m) / 1_000_000.0


def _dualrun_estimate(models, tasks, repeats, elegance_policy):
    """Projected spend for a dual run (sub-project D): each model is evaluated TWICE (baseline +
    best-shot), so metered CANDIDATE spend doubles; judge spend scales with how many phases are judged
    (both=2, bestshot=1, none=0). Returns (metered_total, judge_spend, metered_models, noisy_models),
    where metered_models are the selected metered candidates (no tuned settings; tuning is
    subscription-only) and noisy_models are the reasoning/omit_temp models a --repeats=1 flip could be
    sampling noise for (sampling_uncontrolled / stochastic_bestshot)."""
    _rows, single_total, _unpriced = estimate(models, tasks, repeats)
    judge_phases = {"both": 2, "bestshot": 1, "none": 0}[elegance_policy]
    judge_spend = _project_judge_spend(tasks, repeats) * judge_phases
    metered_total = single_total * 2
    metered_models = [m.key for m in models if m.is_metered]
    noisy = [m.key for m in models if m.omit_temp or m.reasoning]
    return metered_total, judge_spend, metered_models, noisy


def _cmd_estimate_dualrun(args, tasks) -> int:
    elegance_policy = getattr(args, "elegance", "bestshot")
    models = _dualrun_default_models(args.models)
    rows, _single_total, unpriced = estimate(models, tasks, args.repeats)
    metered_total, judge_spend, metered_models, noisy = _dualrun_estimate(
        models, tasks, args.repeats, elegance_policy)
    judge_phases = {"both": 2, "bestshot": 1, "none": 0}[elegance_policy]
    print("dual-run estimate (each model evaluated TWICE: baseline + best-shot):")
    for key, in_t, out_t, cost, is_unpriced in rows:
        cost_col = "UNPRICED" if is_unpriced else f"${cost * 2:.4f}"
        print(f"  {key:22} in~{in_t * 2:>9}  out~{out_t * 2:>9}  {cost_col}")
    print(f"projected metered candidate spend (x2 phases): ${metered_total:.4f}  (budget ${args.budget:.2f})")
    if metered_models:
        print(f"  metered candidates (no tuned settings; tuning is subscription-only): "
              f"{', '.join(metered_models)}")
    print(f"projected judge spend ({elegance_policy}, x{judge_phases}): ${judge_spend:.4f}  "
          "(metered; counts against the shared --budget)")
    if noisy:
        print(f"note: --repeats>=3 recommended for sampling-uncontrolled / stochastic model(s) "
              f"(reasoning or omit_temp): {', '.join(noisy)} (a --repeats=1 flip may be sampling noise)")
    if unpriced:
        print(f"WARNING: {len(unpriced)} metered model(s) UNPRICED, real spend not in the total "
              f"above: {', '.join(unpriced)}. Set prices or exclude before a live run.")
    return 0 if (metered_total + judge_spend) <= args.budget else 2


def cmd_estimate(args) -> int:
    tasks = corpus.load(selector=getattr(args, "suite", None))
    if getattr(args, "dualrun", False):
        return _cmd_estimate_dualrun(args, tasks)
    models = _select(args.models)
    rows, total, unpriced = estimate(models, tasks, args.repeats)
    for key, in_t, out_t, cost, is_unpriced in rows:
        cost_col = "UNPRICED" if is_unpriced else f"${cost:.4f}"
        print(f"  {key:22} in~{in_t:>8}  out~{out_t:>8}  {cost_col}")
    print(f"projected metered spend: ${total:.2f}  (budget ${args.budget:.2f})")
    judge_spend = _project_judge_spend(tasks, args.repeats)
    print(f"projected judge spend: ${judge_spend:.2f}  (worst case: assumes all cells pass, judge "
          f"emits ~{DEFAULT_OUT_TOKENS} out/cell; metered, counts against --budget)")
    n = len(tasks) * args.repeats
    lo, hi = rank.wilson_ci(n // 2, n) if n else (0.0, 0.0)
    print(f"Wilson 95% CI half-width at p=0.5, n=tasks*repeats={n}: {(hi - lo) / 2:.3f}")
    if unpriced:
        # fail loud: a metered model with no price reads as free but is not.
        print(f"WARNING: {len(unpriced)} metered model(s) UNPRICED, real spend not in the "
              f"total above: {', '.join(unpriced)}. Set prices or exclude before a live run.")
    return 0 if total <= args.budget else 2


# ---- validate-oracles -----------------------------------------------------

def cmd_validate_oracles(args) -> int:
    results = corpus.validate_oracles(corpus.load(selector=getattr(args, "suite", None)),
                                      timeout_s=args.timeout)
    for r in results:
        print(f"  {'ok  ' if r.ok else 'FAIL'} {r.task_id}" + ("" if r.ok else f"   {r.detail}"))
    bad = [r for r in results if not r.ok]
    print(f"{len(results) - len(bad)}/{len(results)} oracles valid")
    return 1 if bad else 0


# ---- validate-suites ------------------------------------------------------

def cmd_validate_suites(args) -> int:
    results = corpus.validate_suites()
    for r in results:
        print(f"[{'ok ' if r.ok else 'BAD'}] {r.task_id}: {r.detail}")
    bad = sum(1 for r in results if not r.ok)
    dj = getattr(args, "disjoint", None)
    if dj:
        parts = [s.strip() for s in dj.split(",")]
        if len(parts) != 2 or not parts[0] or not parts[1]:
            print("error: --disjoint expects exactly A,B (two non-empty selectors)")
            return 2
        try:
            corpus.assert_disjoint(parts[0], parts[1])
            print(f"[ok ] disjoint({parts[0]}, {parts[1]})")
        except ValueError as e:
            print(f"[BAD] {e}")
            bad += 1
    if not results:
        # fail loud vs a vacuous green: 0 suites checked must not read like "all valid".
        print("warning: no suite manifests found; leakage checks not exercised")
    print(f"{sum(1 for r in results if r.ok)}/{len(results)} suite(s) valid")
    return 1 if bad else 0


# ---- preflight ------------------------------------------------------------

async def _gather_served(models, env, lister):
    served = {}
    for gw in sorted({m.gateway for m in models}):
        conn = gateways.resolve(gw, env)
        served[gw] = await lister(conn.base_url, conn.api_key) if conn.ok else None
    return served


def cmd_preflight(args, env=None, chat_transport=None, lister=None) -> int:
    env = os.environ if env is None else env
    chat_transport = chat_transport or http_transport
    lister = lister or list_models
    models = _select(args.models)
    served = asyncio.run(_gather_served(models, env, lister))
    result = asyncio.run(preflight_mod.run_all(models, env, chat_transport, served))
    for issue in result.gateway_issues:
        print(f"  GATEWAY  {issue}")
    for key, reason in result.excluded:
        print(f"  EXCLUDE  {key}: {reason}")
    for key in result.reasoning_downgrades:
        print(f"  DOWNGRADE {key}: reasoning -> non-reasoning (zero reasoning_tokens)")
    for note in result.notes:
        print(f"  note     {note}")
    print(f"usable: {len(result.usable)}/{len(models)}; gateway issues: {len(result.gateway_issues)}")
    return 0 if result.ok else 1


# ---- run ------------------------------------------------------------------

def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _persist_raw(raw_dir, spec, tr):
    # Per-(model, task, repeat) filename (A13): --repeats>1 would otherwise collide 3 files onto one
    # path and silently drop 2/3 of the evidence trail. Idempotent: the early candidate-only write and
    # the later judged re-persist resolve to the SAME path for the same run (A7).
    path = os.path.join(raw_dir, f"{spec.key}__{tr.call.task_id}__r{tr.repeat_idx}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "model": spec.key, "task": tr.call.task_id, "repeat_idx": tr.repeat_idx,
            "passed": tr.score.passed, "error_type": tr.score.error_type,
            "latency_s": tr.call.total_latency_s, "cache_hit": tr.call.cache_hit,
            "cost_usd": tr.call.cost_usd,
            "prompt_tokens": tr.call.prompt_tokens, "completion_tokens": tr.call.completion_tokens,
            "thinking_tokens": tr.call.thinking_tokens,
            "extracted_code": tr.call.extracted_code, "raw_response": tr.call.raw_response,
            "elegance": tr.elegance, "elegance_rationale": tr.elegance_rationale,
            "judge_cost_usd": tr.judge_cost_usd,
        }, f, indent=2)


def _phase_metrics(raw_dir: str, repeats: int, spec: ModelSpec) -> dict:
    """Reconstruct per-task TaskMetrics for ONE phase from run_bakeoff's persisted raw (sub-project D).

    Reads every <spec.key>__<task>__r<rep>.json in raw_dir, groups by task_id, and applies the pinned
    inclusion CONTRACT (D6): a task is a key ONLY IF it has EXACTLY `repeats` files AND none is
    operational (error_type in OPERATIONAL_ERROR_TYPES). An operational repeat OR a partial repeat count
    (budget truncation) OMITS the task ENTIRELY, so it never enters the paired pairing as a spurious
    False. cost_proxy_usd is priced with the passed (drift-corrected) `spec` because the sticker price is
    not in the raw JSON and MUST be the same spec the rest of D uses for this model."""
    by_task: dict = {}
    if not os.path.isdir(raw_dir):
        return {}
    for fn in sorted(os.listdir(raw_dir)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(raw_dir, fn), "r", encoding="utf-8") as f:
            rec = json.load(f)
        if rec.get("model") != spec.key:   # phase raw dirs are single-model, but stay defensive
            continue
        by_task.setdefault(rec["task"], []).append(rec)

    out: dict = {}
    for task, recs in by_task.items():
        if len(recs) != repeats:
            continue   # partial (e.g. budget-truncated) -> OMIT, never a False
        if any(r.get("error_type") in OPERATIONAL_ERROR_TYPES for r in recs):
            continue   # operational (provider) failure -> OMIT, never a False
        n_passed = sum(1 for r in recs if r.get("passed"))
        lat = [r["latency_s"] for r in recs
               if r.get("latency_s") is not None and not r.get("cache_hit")]
        cost_proxy = sum(client.cost_proxy_usd(spec, r.get("completion_tokens", 0) or 0,
                                               r.get("thinking_tokens", 0) or 0)
                         for r in recs) / repeats
        elegs = [r["elegance"] for r in recs if r.get("elegance") is not None]
        out[task] = TaskMetric(
            passed=(n_passed == repeats),
            latency_s=(statistics.median(lat) if lat else None),
            cost_proxy_usd=cost_proxy,
            elegance=(statistics.mean(elegs) if elegs else None),
            pass_rate=n_passed / repeats,
        )
    return out


def _accumulate_task_counts(all_runs, pass_counts, attempted_counts):
    """Fold one model's runs into the per-task contamination tallies (SPEC §4). The model
    counts as having attempted a task if it produced >= 1 successful (non-error) run of it,
    and as perfectly passing it only if EVERY run of it passed."""
    by_task: dict = {}
    for tr in all_runs:
        by_task.setdefault(tr.call.task_id, []).append(tr)
    for tid, trs in by_task.items():
        if any(tr.call.ok for tr in trs):
            attempted_counts[tid] = attempted_counts.get(tid, 0) + 1
            if all(tr.score.passed for tr in trs):
                pass_counts[tid] = pass_counts.get(tid, 0) + 1


# A genuine in-band reasoning trace is a CLOSED block, not a bare tag in prose/code (M1): require a
# matching </think> etc. so `# parses <think> elements` is not mistaken for reasoning.
_THINK_BLOCK = re.compile(r"<(think|thinking|reasoning)\b[^>]*>[\s\S]*?</\1>", re.IGNORECASE)


def _showed_reasoning(call) -> bool:
    """True if a call shows reasoning as structured thinking tokens OR a CLOSED in-band
    <think>...</think> block. deepseek reports via tokens; kimi/qwen3.5 in-band (closed tags)."""
    if call.thinking_tokens > 0:
        return True
    return bool(_THINK_BLOCK.search(call.raw_response or ""))


def _finalize_model(spec, all_runs, pass_counts, attempted_counts, notes):
    """Per-model post-processing run on BOTH the normal and budget-stop exit paths:
    accumulate contamination tallies, and fail loud if a reasoning model never showed any
    reasoning across its successful runs (covers subscription models the probe skips)."""
    _accumulate_task_counts(all_runs, pass_counts, attempted_counts)
    ok_runs = [tr for tr in all_runs if tr.call.ok]
    if spec.reasoning and ok_runs and not any(_showed_reasoning(tr.call) for tr in ok_runs):
        notes.append(f"WARNING: reasoning model {spec.key} showed no thinking tokens or "
                     f"<think> blocks across {len(ok_runs)} successful run(s); it may not be "
                     "reasoning. Verify the gateway reasoning controls.")


def _read_prompt(task) -> str:
    with open(task.prompt_path, "r", encoding="utf-8") as f:
        return f.read()


def _emit_judge_escalation(att, unp, notes):
    """Fail-loud tiered summary of judge-parse failures (A21/A22). `att`/`unp` are per-model dicts of
    200-responses dispatched / of-those-unparseable (call-errors are excluded upstream and already
    surfaced per-cell, A10/A22). Per-model tier (floor 3) catches ONE model going dark inside an
    otherwise-healthy run; run-wide tier (floor 5) is the rollup + home of the scattered-miss note."""
    tot_att = sum(att.values())
    tot_unp = sum(unp.values())
    for m in sorted(att):
        a = att[m]
        u = unp.get(m, 0)
        j = a - u
        if a >= 3:
            if u == a:
                notes.append(f"WARNING: elegance axis for {m} is UNAVAILABLE -- all {a} of its "
                             "judged cells returned HTTP 200 but unparseable (suspect this model's "
                             "output format vs the judge parser). Its elegance scores are absent; "
                             "do not rank it on elegance.")
            elif u / a >= 0.8 or j < 2:
                notes.append(f"WARNING: near-total judge-parse failure for {m} -- only {j} of {a} "
                             "of its cells parsed; its elegance axis is UNRELIABLE.")
    if tot_att >= 5 and tot_unp == tot_att:
        notes.append(f"WARNING: ALL {tot_att} judge replies across the run were HTTP 200 but "
                     "unparseable; the elegance axis is UNAVAILABLE run-wide -- suspect a judge "
                     "prompt/template regression or a judge-model outage. Do NOT trust any elegance "
                     "ranking.")
    elif tot_att >= 5 and (tot_unp / tot_att >= 0.8 or (tot_att - tot_unp) < 3):
        notes.append(f"WARNING: near-total judge-parse failure run-wide -- only {tot_att - tot_unp} "
                     f"of {tot_att} judge replies parsed; treat the elegance axis as UNRELIABLE.")
    elif tot_unp > 0:
        notes.append(f"note: {tot_unp} of {tot_att} judge replies were 200 but unparseable "
                     "(elegance omitted for those cells; see any per-model WARNINGs above for "
                     "concentrated failures).")


async def _judge_runs(models, model_runs, task_by_id, jspec, judge_conn, transport, budget,
                      notes, raw_dir):
    """Post-matrix round-robin elegance judging (A7 step 3). Judges every PASSING run, interleaved
    by model so a budget stop truncates every model's tail equally. Attaches elegance in memory and
    RE-PERSISTS each judged cell's file in place; tracks per-model 200-response parse stats (A22)
    and emits the tiered escalation summary (A21). Genuine call errors get a loud per-cell note and
    are NOT counted toward the parse-rate denominator (A10/A22)."""
    spec_by_key = {m.key: m for m in models}
    passing = {k: [tr for tr in runs if tr.score.passed] for k, runs in model_runs.items()}
    maxlen = max((len(v) for v in passing.values()), default=0)
    queue = [(k, passing[k][i]) for i in range(maxlen) for k in model_runs if i < len(passing[k])]
    att, unp = {}, {}
    n_total, n_judged = len(queue), 0
    for key, tr in queue:
        if budget.remaining() <= 0:   # pre-check: do not spend past the cap; leave the tail unjudged
            notes.append(f"judge budget exhausted after {n_judged} of {n_total} passing cells; "
                         "remaining elegance left None")
            break
        task = task_by_id[tr.call.task_id]
        res = await judge_mod.judge_elegance(
            jspec, _read_prompt(task), tr.call.extracted_code,
            judge_conn.api_key, judge_conn.base_url, transport)
        tr.elegance, tr.elegance_rationale, tr.judge_cost_usd = res.elegance, res.rationale, res.cost_usd
        _persist_raw(raw_dir, spec_by_key[key], tr)   # patch the durable file with the verdict
        n_judged += 1
        if not res.call_ok:
            notes.append(f"WARNING: judge CALL error on {key}/{tr.call.task_id} "
                         f"(gateway/wire_id): {res.error}")
        else:
            att[key] = att.get(key, 0) + 1
            if res.elegance is None:
                unp[key] = unp.get(key, 0) + 1
        try:
            budget.add(res.cost_usd)
        except runner.BudgetExceeded as exc:
            notes.append(f"judge budget exhausted after {n_judged} of {n_total} passing cells; "
                         f"remaining elegance left None: {exc}")
            break
    _emit_judge_escalation(att, unp, notes)


async def run_bakeoff(models, tasks, env, out_dir, budget_usd, repeats, transport,
                      bar=0.8, sandbox_timeout=60, judge_spec=None, ceiling_on=True, suite=None,
                      judge_enabled=True):
    os.makedirs(os.path.join(out_dir, "raw"), exist_ok=True)
    raw_dir = os.path.join(out_dir, "raw")
    budget = runner.BudgetTracker(budget_usd)
    ping, notes = {}, []
    pass_counts, attempted_counts = {}, {}

    # No-self-grade guard (A4) + judge gateway resolve, ONLY when judging is enabled (Task 7). Only
    # COMPETITIVELY-RANKED candidates count: the ceiling is a reference bound (excluded from the real
    # run via --no-ceiling), so a judge sharing its family does not bias the candidate ranking. When
    # judge_enabled is False this is a GENUINE no-op: no guard (a same-family judge cannot raise), no
    # gateway resolve, and Phase 2 is skipped below. Default True keeps every shipped caller byte-identical.
    jspec, judge_conn = None, None
    if judge_enabled:
        jspec = judge_spec or registry.judge_spec()
        contenders = [m.key for m in models if not m.is_ceiling]
        if judge_mod.judge_conflicts(jspec.key, contenders):
            raise ValueError(f"judge {jspec.key} shares a model family with a candidate in "
                             f"{contenders}; that would be self-grading. Choose a cross-family judge.")
        judge_conn = gateways.resolve(jspec.gateway, env)

    # PHASE 1: run candidates per model; persist each model's raw runs IMMEDIATELY (A7) so a crash
    # during judging never loses generated solutions. Collect in memory for judging + aggregation.
    model_runs = {}
    for spec in models:
        conn = gateways.resolve(spec.gateway, env)
        if not conn.ok:
            notes.append(f"skipped {spec.key}: gateway {spec.gateway} unconfigured")
            continue
        all_runs = []
        rep_idx = 0
        try:
            for rep_idx in range(repeats):
                runs, warmups = await runner.run_model(
                    spec, tasks, conn.api_key, conn.base_url, transport,
                    budget=budget, sandbox_timeout=sandbox_timeout)
                for tr in runs:
                    tr.repeat_idx = rep_idx
                all_runs += runs
                if warmups and spec.key not in ping:
                    ping[spec.key] = warmups[0].total_latency_s
        except runner.BudgetExceeded as exc:
            partials = getattr(exc, "partial_runs", [])
            for tr in partials:
                tr.repeat_idx = rep_idx   # loop var holds the repeat that was in flight (A13)
            all_runs += partials
            warmups = getattr(exc, "partial_warmups", [])
            if warmups and spec.key not in ping:
                ping[spec.key] = warmups[0].total_latency_s
            notes.append(f"BUDGET STOP at {spec.key}: {exc}")  # fail loud, keep partial
            model_runs[spec.key] = all_runs
            for tr in all_runs:
                _persist_raw(raw_dir, spec, tr)
            break
        model_runs[spec.key] = all_runs
        for tr in all_runs:
            _persist_raw(raw_dir, spec, tr)

    # PHASE 2: elegance judging (patches the persisted files in place).
    if judge_enabled and judge_conn.ok:
        task_by_id = {t.task_id: t for t in tasks}
        await _judge_runs(models, model_runs, task_by_id, jspec, judge_conn, transport,
                          budget, notes, raw_dir)
    elif not judge_enabled:
        notes.append("elegance skipped (phase judging disabled)")
    else:
        notes.append("elegance skipped: judge gateway unconfigured")

    # PHASE 3: aggregate + finalize per model (every model that produced runs still appears).
    aggregates = []
    for spec in models:
        runs = model_runs.get(spec.key)
        if runs is None:
            continue
        # A8: distinguish "no successful runs" from "all runs cache-hit-excluded" for p50.
        agg = runner.aggregate(spec, runs)
        if agg.p50_latency_s is None and agg.n_latency_samples == 0 and any(tr.call.ok for tr in runs):
            notes.append(f"note: {spec.key} p50 is n/a because every successful run looked like a "
                         "cache hit (<100ms); latency not comparable for this model.")
        aggregates.append(agg)
        _finalize_model(spec, runs, pass_counts, attempted_counts, notes)

    # Ceiling phantom guard (A4 step 5): only pin the ceiling if it actually ran.
    ceil = registry.ceiling()
    ceil_key = None
    if ceiling_on and ceil is not None:
        if any(a.model_key == ceil.key for a in aggregates):
            ceil_key = ceil.key
        else:
            notes.append(f"WARNING: ceiling {ceil.key} requested but not in this run's models; "
                         "omitting it from the ladder rather than pinning a phantom entry with no data.")

    result = rank.assemble(aggregates, ceiling_key=ceil_key, bar=bar)
    result.contamination_flags = rank.detect_contamination(pass_counts, attempted_counts)
    if result.contamination_flags:
        notes.append("CONTAMINATION: tasks passed perfectly by >= 75% of healthy testers; "
                     "review or exclude before trusting the ladder: "
                     f"{', '.join(result.contamination_flags)}.")
    result.notes.extend(notes)
    run_id = os.path.basename(out_dir.rstrip("/"))
    suite_record = {"selector": suite, "task_ids": [t.task_id for t in tasks]}
    _write(os.path.join(out_dir, "report.md"),
           report.render_report_md(result, run_id, len(tasks), suite_selector=suite))
    _write(os.path.join(out_dir, "ladder.yaml"), report.render_ladder_yaml(result.ladder))
    _write(os.path.join(out_dir, "summary.json"),
           report.render_summary_json(result, run_id=run_id, n_tasks=len(tasks),
                                       ping_baselines=ping, budget_spent=budget.spent,
                                       suite=suite_record))
    return result, budget.spent


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def cmd_run(args, env=None, transport=None) -> int:
    env = os.environ if env is None else env
    transport = transport or http_transport
    suite = getattr(args, "suite", None)
    tasks, models = corpus.load(selector=suite), _select(args.models)
    out_dir = args.out or os.path.join(os.path.dirname(__file__), "runs", _run_id())
    os.makedirs(out_dir, exist_ok=True)
    jspec = registry.judge_spec()
    judge_key = getattr(args, "judge", None)
    if judge_key and judge_key != jspec.key:
        jspec = dataclasses.replace(jspec, key=judge_key, wire_id=judge_key)
    result, spent = asyncio.run(run_bakeoff(
        models, tasks, env, out_dir, args.budget, args.repeats, transport,
        bar=getattr(args, "bar", 0.8), sandbox_timeout=getattr(args, "sandbox_timeout", 60),
        judge_spec=jspec, ceiling_on=not getattr(args, "no_ceiling", False), suite=suite))
    print(f"wrote {out_dir}  (metered spend ${spent:.4f})")
    print(f"ladder: {' -> '.join(result.ladder)}")
    return 0


# ---- dualrun (sub-project D) ----------------------------------------------

def _dualrun_default_models(models_arg):
    """dualrun's default model set: the SUBSCRIPTION subset. The three metered models are
    unconditionally skipped by C's tuner, so they can never carry a tuned record; an explicit --models
    list is honoured verbatim (a metered key then runs with a loud note and inherits the budget guards)."""
    if models_arg:
        return _select(models_arg)
    return [m for m in registry.ROSTER if not m.is_metered]


def _load_tuned_record(settings_dir, key):
    p = os.path.join(settings_dir, key, "best_settings.json")
    if not os.path.isfile(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def cmd_dualrun(args, env=None, transport=None) -> int:
    """Dual-run benchmark (sub-project D): evaluate each model on the SCORED corpus twice, at equal-
    footing baseline settings and at C's reconstructed best-shot settings, interleaved per model. Reports
    the per-model tuning delta on all five axes with a paired McNemar verdict, honest noise disclosure,
    leakage attestation, order-confound control, low-confidence propagation, and crash-safe incremental
    persistence. Offline-testable (injected transport). The two phases are two single-model run_bakeoff
    calls sharing --budget by SUBTRACTION."""
    env = os.environ if env is None else env
    transport = transport or http_transport
    suite = getattr(args, "suite", None)
    settings_dir = args.settings_dir
    tasks_dir = getattr(args, "tasks_dir", None)   # scored corpus dir (default: the standard tasks/)
    out = args.out or os.path.join(os.path.dirname(__file__), "runs", _run_id() + "-dualrun")
    os.makedirs(out, exist_ok=True)

    tasks = corpus.load(selector=suite, tasks_dir=tasks_dir)
    scored_dir = tasks_dir or corpus.default_tasks_dir()
    n_tasks = len(tasks)

    # D4 leakage attestation: scored dir vs dev dir, or vs dev_corpus.json ids when the dir is gone.
    dev_dir = getattr(args, "dev_tasks_dir", None)
    dev_corpus = None
    dcp = os.path.join(settings_dir, "dev_corpus.json")
    if os.path.isfile(dcp):
        try:
            with open(dcp, "r", encoding="utf-8") as f:
                dev_corpus = json.load(f)
        except (OSError, json.JSONDecodeError):
            dev_corpus = None
    leakage_checked = False
    try:
        if dev_dir and os.path.isdir(dev_dir):
            corpus.assert_disjoint_dirs(scored_dir, dev_dir)
            leakage_checked = True
        elif dev_corpus and dev_corpus.get("dev_tasks"):
            corpus.assert_disjoint_dirs(scored_dir, None, ids_b=set(dev_corpus["dev_tasks"]))
            leakage_checked = True
    except ValueError as e:
        print(f"error: scored corpus leaks into the dev corpus: {e}")
        return 2
    if not leakage_checked:
        print("warning: no dev corpus dir or dev_corpus.json; leakage not attested (not blocking)")

    models = _dualrun_default_models(args.models)
    if getattr(args, "no_ceiling", False):
        models = [m for m in models if not m.is_ceiling]
    bestshot_specs, drift_notes = settings_loader.load_tuned_specs(settings_dir, models, env)
    n_tuned_records = sum(1 for m in models if _load_tuned_record(settings_dir, m.key) is not None)

    run_notes = list(drift_notes)
    metered_selected = [m.key for m in models if m.is_metered]
    if metered_selected:
        note = ("note: metered model(s) selected; they run against the shared budget and have no tuned "
                f"record (tuning is subscription-only): {', '.join(metered_selected)}")
        print(note)
        run_notes.append(note)

    elegance_policy = getattr(args, "elegance", "bestshot")
    order = getattr(args, "order", "alternate")
    repeats = getattr(args, "repeats", 1)
    bar = getattr(args, "bar", 0.8)
    sandbox_timeout = getattr(args, "sandbox_timeout", 60)
    total_budget = args.budget

    jspec = registry.judge_spec()
    judge_key = getattr(args, "judge", None)
    if judge_key and judge_key != jspec.key:
        jspec = dataclasses.replace(jspec, key=judge_key, wire_id=judge_key)

    provenance = {"scored_dir": scored_dir, "dev_dir": dev_dir,
                  "leakage_checked": leakage_checked, "dev_corpus": dev_corpus}
    rows, phase_metrics, orders = [], {}, {}
    budget_state = {"spent": 0.0}
    run_id = os.path.basename(out.rstrip("/"))

    def _persist_combined():
        _write(os.path.join(out, "dualrun.md"), report.render_dualrun_md(
            rows, run_id=run_id, suite_selector=suite, n_tasks=n_tasks, elegance_policy=elegance_policy,
            order=order, provenance=provenance, drift_notes=drift_notes, run_notes=run_notes,
            orders=orders, n_tuned_records=n_tuned_records))
        _write(os.path.join(out, "dualrun_summary.json"), report.render_dualrun_summary_json(
            rows, phase_metrics=phase_metrics, run_id=run_id, suite_selector=suite, n_tasks=n_tasks,
            elegance_policy=elegance_policy, order=order, provenance=provenance, run_notes=run_notes,
            orders=orders, n_tuned_records=n_tuned_records))

    async def _one_phase(run_spec, phase_label, judge_this):
        phase_out = os.path.join(out, run_spec.key, phase_label)
        os.makedirs(phase_out, exist_ok=True)
        remaining = max(0.0, total_budget - budget_state["spent"])   # shared budget by SUBTRACTION
        result, spent = await run_bakeoff(
            [run_spec], tasks, env, phase_out, remaining, repeats, transport,
            bar=bar, sandbox_timeout=sandbox_timeout, judge_spec=jspec, ceiling_on=False,
            suite=suite, judge_enabled=judge_this)
        budget_state["spent"] += spent
        run_notes.extend(f"[{run_spec.key}/{phase_label}] {n}" for n in result.notes)
        agg = next((a for a in result.report_rows if a.model_key == run_spec.key), None)
        metrics = _phase_metrics(os.path.join(phase_out, "raw"), repeats, run_spec)
        return agg, metrics

    async def _run_model(spec, bestshot_spec, baseline_first, judge_baseline, judge_bestshot):
        plan = [("baseline", spec, judge_baseline), ("bestshot", bestshot_spec, judge_bestshot)]
        if not baseline_first:
            plan = list(reversed(plan))
        got = {}
        for label, run_spec, judge_this in plan:
            got[label] = await _one_phase(run_spec, label, judge_this)
        return got

    for i, spec in enumerate(models):
        bestshot_spec = bestshot_specs[spec.key]
        judge_baseline = elegance_policy == "both"
        judge_bestshot = elegance_policy in ("both", "bestshot")
        if order == "baseline-first":
            baseline_first = True
        elif order == "bestshot-first":
            baseline_first = False
        else:                                       # alternate: even-index baseline-first, odd bestshot-first
            baseline_first = (i % 2 == 0)
        orders[spec.key] = "baseline-first" if baseline_first else "bestshot-first"

        got = asyncio.run(_run_model(spec, bestshot_spec, baseline_first, judge_baseline, judge_bestshot))
        baseline_agg, baseline_metrics = got["baseline"]
        bestshot_agg, bestshot_metrics = got["bestshot"]
        if baseline_agg is None or bestshot_agg is None:
            missing = [lbl for lbl, (a, _m) in got.items() if a is None]
            note = (f"note: excluded {spec.key} from the delta table; no aggregate for phase(s) "
                    f"{missing} (gateway unconfigured or no completed runs)")
            print(note)
            run_notes.append(note)
            _persist_combined()               # crash-safe: keep 1..N-1 even when N is excluded
            continue

        row = rank.tuning_delta(baseline_agg, bestshot_agg, baseline_metrics, bestshot_metrics,
                                spec, bestshot_spec, repeats)
        rec = _load_tuned_record(settings_dir, spec.key)
        if rec:                                    # D7: propagate the canonical low-confidence verdict
            row.low_confidence = bool(rec.get("low_confidence"))
            row.low_confidence_reasons = list(rec.get("reasons", []))
        rows.append(row)
        phase_metrics[spec.key] = {"baseline": baseline_metrics, "bestshot": bestshot_metrics}
        _persist_combined()

    _persist_combined()
    print(f"wrote {out}")
    print(f"dual-run metered spend ${budget_state['spent']:.4f}  ({len(rows)} model delta(s))")
    return 0


# ---- tune (sub-project C) -------------------------------------------------

def _parse_try_gateways(items):
    """Parse repeated --try-gateway 'gateway:wire_id' into (gateway, wire_id) pairs. split(":", 1)
    keeps colon-bearing wire_ids intact (e.g. ollama-cloud:qwen3.5:397b)."""
    out = []
    for it in items or []:
        gw, wid = it.split(":", 1)
        out.append((gw, wid))
    return out


def cmd_tune(args, env=None, transport=None) -> int:
    """Subscription-only best-shot tuning (sub-project C). Refuses metered models (recorded in
    SKIPPED.json), enforces a task-id leakage guard between the dev and scored suites, and writes a
    per-model best_settings.json. Oracle-only: no judge is ever constructed."""
    env = os.environ if env is None else env
    transport = transport or http_transport
    out = args.out or os.path.join(os.path.dirname(__file__), "runs", _run_id() + "-tune")
    os.makedirs(out, exist_ok=True)
    suite = getattr(args, "suite", None)
    tasks_dir = getattr(args, "tasks_dir", None)
    suites_dir = getattr(args, "suites_dir", None)
    tasks = corpus.load(selector=suite, tasks_dir=tasks_dir, suites_dir=suites_dir)

    against = getattr(args, "against", None)
    if against:
        try:
            corpus.assert_disjoint(suite, against, tasks_dir=tasks_dir, suites_dir=suites_dir)
        except ValueError as e:
            print(f"error: dev and scored suites are not disjoint: {e}")
            return 2

    models = _select(args.models)
    metered = [m for m in models if m.is_metered]
    subscription = [m for m in models if not m.is_metered]
    skipped = [{"key": m.key, "reason": "metered (tuning is subscription-only; no budget cap)"}
               for m in metered]
    if metered:
        print(f"refusing to tune metered model(s): {', '.join(m.key for m in metered)} "
              "(tuning is subscription-only). Recorded in SKIPPED.json.")

    def conn_for(gw):
        return gateways.resolve(gw, env)

    extra_gateways = _parse_try_gateways(getattr(args, "try_gateway", None))
    for spec in subscription:
        if not conn_for(spec.gateway).ok:
            print(f"skipping {spec.key}: gateway {spec.gateway} unconfigured")
            skipped.append({"key": spec.key, "reason": f"gateway {spec.gateway} unconfigured"})
            continue
        rec = asyncio.run(tuning.tune_model(
            spec, tasks, conn_for, transport, os.path.join(out, spec.key),
            extra_gateways=extra_gateways, repeats=args.repeats))
        print(f"tuned {spec.key}: winner pass {rec['achieved']['pass_fraction']:.0%}, "
              f"{rec['neighbours_evaluated']} neighbours evaluated"
              + (" [LOW CONFIDENCE: " + ", ".join(rec["reasons"]) + "]" if rec["low_confidence"] else ""))

    if skipped:
        _write(os.path.join(out, "SKIPPED.json"), json.dumps(skipped, indent=2) + "\n")
    tuned = [m for m in subscription if not any(s["key"] == m.key for s in skipped)]
    if not tuned:
        print("no subscription models tuned (all selected models were metered or unconfigured).")
        return 2
    print(f"wrote {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="model_bakeoff")
    sub = p.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate-oracles")
    v.add_argument("--timeout", type=int, default=60)
    v.add_argument("--suite", default=None, help="restrict to a suite: tag:<t> or a manifest name")
    v.set_defaults(func=cmd_validate_oracles)

    vs = sub.add_parser("validate-suites")
    vs.add_argument("--disjoint", default=None, metavar="A,B",
                    help="check two selectors are disjoint; do not pass 'all' (always overlaps)")
    vs.set_defaults(func=cmd_validate_suites)

    e = sub.add_parser("estimate")
    e.add_argument("--models", default="")
    e.add_argument("--repeats", type=int, default=1,
                   help="repetitions per task (default 1; use 3+ for tighter CIs at higher cost)")
    e.add_argument("--budget", type=float, default=10.0)
    e.add_argument("--suite", default=None, help="restrict to a suite: tag:<t> or a manifest name")
    e.add_argument("--dualrun", action="store_true",
                   help="estimate a sub-project D dual run (each model evaluated twice: baseline + best-shot)")
    e.add_argument("--elegance", choices=["both", "bestshot", "none"], default="bestshot",
                   help="dual-run: which phase(s) the judge scores (drives the doubled judge spend)")
    e.set_defaults(func=cmd_estimate)

    pf = sub.add_parser("preflight")
    pf.add_argument("--models", default="")
    pf.set_defaults(func=cmd_preflight)

    r = sub.add_parser("run")
    r.add_argument("--models", default="")
    r.add_argument("--repeats", type=int, default=1,
                   help="repetitions per task (default 1; use 3+ for tighter CIs at higher cost)")
    r.add_argument("--budget", type=float, default=10.0)
    r.add_argument("--bar", type=float, default=0.8,
                   help="min pass_fraction to keep a model in the ladder (SPEC §4 standard/thorough)")
    r.add_argument("--sandbox-timeout", type=int, default=60,
                   help="per-task sandbox wall-clock timeout in seconds")
    r.add_argument("--judge", default=registry.judge_spec().key,
                   help="elegance judge key (must be cross-family vs all candidates)")
    r.add_argument("--no-ceiling", action="store_true",
                   help="do not pin the ceiling model in the ladder (use for a candidates-only run)")
    r.add_argument("--suite", default=None, help="restrict to a suite: tag:<t> or a manifest name")
    r.add_argument("--out", default="")
    r.set_defaults(func=cmd_run)

    dr = sub.add_parser("dualrun")
    dr.add_argument("--models", default="",
                    help="default: the subscription subset (metered models are excluded)")
    dr.add_argument("--suite", default=None, help="restrict the scored corpus: tag:<t> or a manifest name")
    dr.add_argument("--settings-dir", required=True,
                    help="dir of C's per-model best_settings.json (the tuned best-shot source)")
    dr.add_argument("--tasks-dir", default=None,
                    help="scored corpus dir (defaults to the standard tasks/); the dev corpus must be disjoint")
    dr.add_argument("--dev-tasks-dir", default=None,
                    help="the tuner's dev corpus dir (leakage guard vs the scored corpus)")
    dr.add_argument("--budget", type=float, default=10.0, help="TOTAL metered cap across BOTH phases")
    dr.add_argument("--elegance", choices=["both", "bestshot", "none"], default="bestshot",
                    help="which phase(s) the elegance judge scores (default: best-shot only)")
    dr.add_argument("--order", choices=["alternate", "baseline-first", "bestshot-first"],
                    default="alternate", help="per-model phase order (alternate counterbalances the fleet)")
    dr.add_argument("--judge", default=registry.judge_spec().key,
                    help="elegance judge key (must be cross-family vs all candidates)")
    dr.add_argument("--no-ceiling", action="store_true",
                    help="drop the ceiling model if it was explicitly selected (moot under the default)")
    dr.add_argument("--repeats", type=int, default=1,
                    help="repetitions per task per phase (default 1; use 3+ for stochastic models)")
    dr.add_argument("--bar", type=float, default=0.8)
    dr.add_argument("--sandbox-timeout", type=int, default=60)
    dr.add_argument("--out", default="")
    dr.set_defaults(func=cmd_dualrun)

    t = sub.add_parser("tune")
    t.add_argument("--models", default="")
    t.add_argument("--suite", default=None, help="dev suite selector: tag:<t> or a manifest name")
    t.add_argument("--against", default=None, metavar="SCORED",
                   help="scored suite the dev suite must be disjoint from (leakage guard)")
    t.add_argument("--tasks-dir", default=None, help="dev corpus dir (defaults to the standard tasks/)")
    t.add_argument("--suites-dir", default=None)
    t.add_argument("--try-gateway", action="append", default=None, metavar="GW:WIRE",
                   help="also search this gateway:wire_id provider; repeatable; metered gateways dropped")
    t.add_argument("--repeats", type=int, default=2,
                   help="repetitions per candidate (2 to 3 for stochastic stability)")
    t.add_argument("--out", default="")
    t.set_defaults(func=cmd_tune)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
