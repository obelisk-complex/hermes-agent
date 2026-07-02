"""Orchestration: run the roster over the corpus (SPEC §5, §10).

Per model: warm up immediately before its batch (no idle gap, SPEC §10 BM3), run
every task through call -> extract -> sandbox -> score, enforce the metered budget
cap as we go (SPEC §8), and aggregate. Offline-testable: the client transport is
injected, so a fake transport plus a tiny temp corpus drives the whole
call->score pipeline with no network (the sandbox still runs real subprocesses).
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Optional

from . import client, extractor, sandbox, scorer
from .models import (ERR_CALL, OPERATIONAL_ERROR_TYPES, CallResult, ModelAggregate, ModelSpec,
                     ScoreResult, TaskSpec)


class BudgetExceeded(Exception):
    """Raised when cumulative metered spend would exceed the hard cap (SPEC §8). Carries the
    stopped model's already-completed runs/warmups so the caller persists them (SPEC §8, M3)."""

    def __init__(self, *args, partial_runs=None, partial_warmups=None):
        super().__init__(*args)
        self.partial_runs = list(partial_runs or [])
        self.partial_warmups = list(partial_warmups or [])


class BudgetTracker:
    def __init__(self, cap_usd: float):
        self.cap = cap_usd
        self.spent = 0.0

    def add(self, cost: float) -> None:
        self.spent += max(0.0, cost)
        if self.spent > self.cap:
            raise BudgetExceeded(f"metered spend ${self.spent:.4f} exceeds cap ${self.cap:.2f}")

    def remaining(self) -> float:
        return max(0.0, self.cap - self.spent)


def should_rewarm(first_s: Optional[float], second_s: Optional[float], factor: float = 2.0) -> bool:
    # SPEC §10 PM2: two warm-up inference calls; re-warm if the 2nd is anomalously
    # slower than the 1st (model went cold again). Both-slow edge case is accepted.
    return first_s is not None and second_s is not None and second_s > factor * first_s


@dataclass
class TaskRun:
    call: CallResult
    score: ScoreResult
    # Coding-bakeoff additive fields (report-only; do not affect the pass_fraction ladder).
    elegance: Optional[float] = None        # LLM-judge elegance score in [0,1], None if unjudged
    elegance_rationale: str = ""
    judge_cost_usd: float = 0.0             # metered judge spend attributed to this cell
    repeat_idx: int = 0                     # which --repeats pass produced this run (A13); stamped in run_bakeoff


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


async def run_one(spec: ModelSpec, task: TaskSpec, api_key: str, base_url: str,
                  transport, sandbox_timeout: Optional[int] = None) -> TaskRun:
    prompt = _read(task.prompt_path)
    call = await client.call(spec, task.task_id, prompt, api_key, base_url, transport,
                             reasoning_extras=spec.reasoning_extras)
    if not call.ok:
        return TaskRun(call, ScoreResult(spec.key, task.task_id, passed=False,
                                         error_type=ERR_CALL, detail=call.error))
    ext = extractor.extract(call.raw_response)
    call.extracted_code = ext.code
    call.extraction_failed = ext.failed
    sb = None
    if not ext.failed:
        sb = sandbox.run(ext.code, task.oracle_path,
                         timeout_s=sandbox_timeout if sandbox_timeout is not None else 60)
    return TaskRun(call, scorer.score(spec.key, task.task_id, ext, sb))


async def run_model(spec: ModelSpec, tasks: list[TaskSpec], api_key: str, base_url: str,
                    transport, budget: Optional[BudgetTracker] = None,
                    sandbox_timeout: Optional[int] = None,
                    warmup_prompt: str = "Reply with the single digit 1.") -> tuple[list[TaskRun], list[CallResult]]:
    # Warm up immediately before this model's batch; two inference calls (SPEC §10).
    warmups: list[CallResult] = []
    w1 = await client.call(spec, "_warmup1", warmup_prompt, api_key, base_url, transport, retry_on_cache_hit=False)
    w2 = await client.call(spec, "_warmup2", warmup_prompt, api_key, base_url, transport, retry_on_cache_hit=False)
    warmups += [w1, w2]
    if should_rewarm(w1.total_latency_s, w2.total_latency_s):
        warmups.append(await client.call(spec, "_warmup3", warmup_prompt, api_key, base_url,
                                          transport, retry_on_cache_hit=False))

    # Warm-up cost (two tiny prompts) is negligible and not metered; task cost is.
    runs: list[TaskRun] = []
    for task in tasks:
        tr = await run_one(spec, task, api_key, base_url, transport, sandbox_timeout)
        runs.append(tr)  # keep the costed run BEFORE the budget check (M3: partials survive a stop)
        if spec.is_metered and budget is not None:
            try:
                budget.add(tr.call.cost_usd)  # may raise BudgetExceeded
            except BudgetExceeded as exc:
                exc.partial_runs = runs
                exc.partial_warmups = warmups
                raise
    return runs, warmups


def aggregate(spec: ModelSpec, runs: list[TaskRun]) -> ModelAggregate:
    n = len(runs)
    n_pass = sum(1 for r in runs if r.score.passed)
    # p50 over cache-hit-CLEAN latencies only (A1): a suspected cache hit (<100ms) is not a real
    # generation latency and would bias speed downward. n_latency_samples lets the report tell
    # "no successful runs" apart from "all runs were cache-hit-excluded" (A8).
    lat = [r.call.total_latency_s for r in runs
           if r.call.total_latency_s is not None and not r.call.cache_hit]
    total_cost = sum(r.call.cost_usd for r in runs)
    # Elegance rollup over judged cells only (unjudged r.elegance is None).
    elegances = [r.elegance for r in runs if r.elegance is not None]
    # Sticker-price cost proxy averaged per task (subscription cost_usd stays 0; this is the only cost axis).
    proxy_total = sum(client.cost_proxy_usd(spec, r.call.completion_tokens, r.call.thinking_tokens)
                      for r in runs)
    # Reliability histogram: bucket every failed run's error_type; count the operational (provider) ones.
    error_counts: dict = {}
    n_operational = 0
    for r in runs:
        et = r.score.error_type
        if et is not None:
            error_counts[et] = error_counts.get(et, 0) + 1
            if et in OPERATIONAL_ERROR_TYPES:
                n_operational += 1
    return ModelAggregate(
        model_key=spec.key, reasoning=spec.reasoning, cost_model=spec.cost_model,
        n_tasks=n, n_passed=n_pass,
        cost_per_task_usd=(total_cost / n if n else 0.0),
        p50_latency_s=(statistics.median(lat) if lat else None),
        n_latency_samples=len(lat),
        mean_elegance=(statistics.mean(elegances) if elegances else None),
        n_elegance_judged=len(elegances),
        cost_proxy_per_task_usd=(proxy_total / n if n else 0.0),
        gateway=spec.gateway, error_counts=error_counts, n_operational=n_operational,
    )
