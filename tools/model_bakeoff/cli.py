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
import sys
from datetime import datetime, timezone

from . import corpus, gateways, preflight as preflight_mod, rank, registry, report, runner
from .http_transport import http_transport, list_models

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


def cmd_estimate(args) -> int:
    tasks, models = corpus.load(), _select(args.models)
    rows, total, unpriced = estimate(models, tasks, args.repeats)
    for key, in_t, out_t, cost, is_unpriced in rows:
        cost_col = "UNPRICED" if is_unpriced else f"${cost:.4f}"
        print(f"  {key:22} in~{in_t:>8}  out~{out_t:>8}  {cost_col}")
    print(f"projected metered spend: ${total:.2f}  (budget ${args.budget:.2f})")
    if unpriced:
        # fail loud: a metered model with no price reads as free but is not.
        print(f"WARNING: {len(unpriced)} metered model(s) UNPRICED, real spend not in the "
              f"total above: {', '.join(unpriced)}. Set prices or exclude before a live run.")
    return 0 if total <= args.budget else 2


# ---- validate-oracles -----------------------------------------------------

def cmd_validate_oracles(args) -> int:
    results = corpus.validate_oracles(corpus.load(), timeout_s=args.timeout)
    for r in results:
        print(f"  {'ok  ' if r.ok else 'FAIL'} {r.task_id}" + ("" if r.ok else f"   {r.detail}"))
    bad = [r for r in results if not r.ok]
    print(f"{len(results) - len(bad)}/{len(results)} oracles valid")
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
    path = os.path.join(raw_dir, f"{spec.key}__{tr.call.task_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "model": spec.key, "task": tr.call.task_id,
            "passed": tr.score.passed, "error_type": tr.score.error_type,
            "latency_s": tr.call.total_latency_s, "cost_usd": tr.call.cost_usd,
            "prompt_tokens": tr.call.prompt_tokens, "completion_tokens": tr.call.completion_tokens,
            "thinking_tokens": tr.call.thinking_tokens,
            "extracted_code": tr.call.extracted_code, "raw_response": tr.call.raw_response,
        }, f, indent=2)


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


async def run_bakeoff(models, tasks, env, out_dir, budget_usd, repeats, transport,
                      bar=0.8, sandbox_timeout=60):
    os.makedirs(os.path.join(out_dir, "raw"), exist_ok=True)
    raw_dir = os.path.join(out_dir, "raw")
    budget = runner.BudgetTracker(budget_usd)
    aggregates, ping, notes = [], {}, []
    pass_counts, attempted_counts = {}, {}
    for spec in models:
        conn = gateways.resolve(spec.gateway, env)
        if not conn.ok:
            notes.append(f"skipped {spec.key}: gateway {spec.gateway} unconfigured")
            continue
        all_runs = []
        try:
            for _ in range(repeats):
                runs, warmups = await runner.run_model(
                    spec, tasks, conn.api_key, conn.base_url, transport,
                    budget=budget, sandbox_timeout=sandbox_timeout)
                all_runs += runs
                if warmups and spec.key not in ping:
                    ping[spec.key] = warmups[0].total_latency_s
        except runner.BudgetExceeded as exc:
            all_runs += getattr(exc, "partial_runs", [])  # M3: keep the costed runs from the stopped pass
            warmups = getattr(exc, "partial_warmups", [])
            if warmups and spec.key not in ping:
                ping[spec.key] = warmups[0].total_latency_s
            notes.append(f"BUDGET STOP at {spec.key}: {exc}")  # fail loud, keep partial
            for tr in all_runs:
                _persist_raw(raw_dir, spec, tr)
            if all_runs:
                aggregates.append(runner.aggregate(spec, all_runs))
            _finalize_model(spec, all_runs, pass_counts, attempted_counts, notes)
            break
        for tr in all_runs:
            _persist_raw(raw_dir, spec, tr)
        aggregates.append(runner.aggregate(spec, all_runs))
        _finalize_model(spec, all_runs, pass_counts, attempted_counts, notes)

    ceil = registry.ceiling()
    result = rank.assemble(aggregates, ceiling_key=ceil.key if ceil else None, bar=bar)
    result.contamination_flags = rank.detect_contamination(pass_counts, attempted_counts)
    if result.contamination_flags:
        notes.append("CONTAMINATION: tasks passed perfectly by >= 75% of healthy testers; "
                     "review or exclude before trusting the ladder: "
                     f"{', '.join(result.contamination_flags)}.")
    result.notes.extend(notes)
    run_id = os.path.basename(out_dir.rstrip("/"))
    _write(os.path.join(out_dir, "report.md"), report.render_report_md(result, run_id, len(tasks)))
    _write(os.path.join(out_dir, "ladder.yaml"), report.render_ladder_yaml(result.ladder))
    _write(os.path.join(out_dir, "summary.json"),
           report.render_summary_json(result, run_id=run_id, n_tasks=len(tasks),
                                       ping_baselines=ping, budget_spent=budget.spent))
    return result, budget.spent


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def cmd_run(args, env=None, transport=None) -> int:
    env = os.environ if env is None else env
    transport = transport or http_transport
    tasks, models = corpus.load(), _select(args.models)
    out_dir = args.out or os.path.join(os.path.dirname(__file__), "runs", _run_id())
    os.makedirs(out_dir, exist_ok=True)
    result, spent = asyncio.run(run_bakeoff(
        models, tasks, env, out_dir, args.budget, args.repeats, transport,
        bar=getattr(args, "bar", 0.8), sandbox_timeout=getattr(args, "sandbox_timeout", 60)))
    print(f"wrote {out_dir}  (metered spend ${spent:.4f})")
    print(f"ladder: {' -> '.join(result.ladder)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="model_bakeoff")
    sub = p.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate-oracles")
    v.add_argument("--timeout", type=int, default=60)
    v.set_defaults(func=cmd_validate_oracles)

    e = sub.add_parser("estimate")
    e.add_argument("--models", default="")
    e.add_argument("--repeats", type=int, default=1,
                   help="repetitions per task (default 1; use 3+ for tighter CIs at higher cost)")
    e.add_argument("--budget", type=float, default=10.0)
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
    r.add_argument("--out", default="")
    r.set_defaults(func=cmd_run)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
