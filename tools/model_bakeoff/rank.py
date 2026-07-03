"""Ranking and ladder assembly (SPEC §2, §9). Pure given the aggregates.

Report ordering: pass_fraction desc, cost_per_task asc, p50 latency asc, name,
computed WITHIN a reasoning group (reasoning and non-reasoning are never merged).
Ladder ordering: weakest-first (pass_fraction asc) with the declared ceiling
pinned last, matching what load_ladder()/next_rung() in the quality-gate expect.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from math import comb, floor
from typing import Iterable, Optional

from .models import LadderResult, ModelAggregate, ModelSpec, TaskMetric


def wilson_ci(passed: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score 95% interval on a pass fraction (SPEC §8/§9)."""
    if n <= 0:
        return (0.0, 0.0)
    p = passed / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


@dataclass
class PairedResult:
    """Exact McNemar test on per-task pass/fail flips between two phases run on the SAME corpus
    (sub-project D). n_paired = tasks completed in BOTH phases; b = passed baseline, failed best-shot
    (a regression); c = failed baseline, passed best-shot (an improvement)."""
    n_paired: int
    b: int
    c: int
    p_value: float
    significant: bool


def paired_significance(baseline_passes: dict[str, bool],
                        bestshot_passes: dict[str, bool]) -> PairedResult:
    """PRIMARY significance for a baseline-vs-best-shot dual run (sub-project D D6a). Baseline and
    best-shot are evaluated on the identical corpus, so this is a PAIRED binary design; the correct
    test is the exact two-sided McNemar on the discordant per-task flips, not a Wilson-CI overlap
    (which is unpaired). Only task_ids present in BOTH maps are paired (an operational/partial task is
    OMITTED upstream, never a False, so it does not enter the pairing). n = b + c discordant pairs;
    p = min(1, 2 * sum_{i=0}^{min(b,c)} C(n,i) * 0.5^n); n == 0 -> p = 1.0. Pure math.comb, no scipy."""
    shared = set(baseline_passes) & set(bestshot_passes)
    b = sum(1 for t in shared if baseline_passes[t] and not bestshot_passes[t])
    c = sum(1 for t in shared if not baseline_passes[t] and bestshot_passes[t])
    n = b + c
    if n == 0:
        p = 1.0
    else:
        k = min(b, c)
        p = min(1.0, 2.0 * sum(comb(n, i) for i in range(k + 1)) * 0.5 ** n)
    return PairedResult(n_paired=len(shared), b=b, c=c, p_value=p, significant=p < 0.05)


@dataclass
class DeltaRow:
    """One model's baseline-vs-best-shot tuning delta (sub-project D). All five axes are paired over
    the completed-in-both intersection (speed as median-of-per-task-differences, cost/elegance as mean
    of per-task differences); accuracy carries the PRIMARY exact-McNemar PairedResult plus a descriptive
    CI-overlap secondary. Every noise/confound/causal flag is a field so the renderer stays declarative.
    low_confidence + reasons are populated by cmd_dualrun from the tuned record (D7); tuning_delta leaves
    them at their defaults."""
    model_key: str
    no_data: bool = False
    no_data_reason: str = ""
    # accuracy (paired over the intersection)
    paired: Optional[PairedResult] = None
    baseline_pass_fraction: Optional[float] = None
    bestshot_pass_fraction: Optional[float] = None
    pass_fraction_delta: Optional[float] = None
    baseline_completed: int = 0
    bestshot_completed: int = 0
    completed_delta: int = 0
    # reliability (corpus-wide by construction; operational tasks never enter the intersection)
    baseline_n_operational: int = 0
    bestshot_n_operational: int = 0
    n_operational_delta: int = 0
    # speed (paired median of per-task diffs) + corpus-wide phase p50 for context
    p50_paired_delta: Optional[float] = None
    baseline_p50_s: Optional[float] = None
    bestshot_p50_s: Optional[float] = None
    # cost (paired proxy diff + real $ diff; proxy is USD-equivalent sticker, not real spend)
    cost_proxy_paired_delta: Optional[float] = None
    cost_usd_delta: float = 0.0
    baseline_cost_proxy_per_task_usd: float = 0.0
    bestshot_cost_proxy_per_task_usd: float = 0.0
    # elegance (paired diff over jointly-judged tasks) + coverage counts
    elegance_paired_delta: Optional[float] = None
    baseline_mean_elegance: Optional[float] = None
    bestshot_mean_elegance: Optional[float] = None
    baseline_n_elegance_judged: int = 0
    bestshot_n_elegance_judged: int = 0
    # descriptive secondary + composition
    ci_overlap: bool = False
    empty_intersection: bool = False
    task_composition_mismatch: bool = False
    n_tasks_mismatch: bool = False
    # causal / noise flags
    baseline_gateway: Optional[str] = None
    bestshot_gateway: Optional[str] = None
    gateway_changed: bool = False
    gateway_capped: bool = False
    tuning_induced_regression: bool = False
    order_confound_suspect: bool = False
    stochastic_bestshot: bool = False
    sampling_uncontrolled: bool = False
    tuned_temperature: Optional[float] = None
    # low-confidence propagation (set post-hoc by cmd_dualrun from the tuned record)
    low_confidence: bool = False
    low_confidence_reasons: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def tuning_delta(baseline: ModelAggregate, bestshot: ModelAggregate,
                 baseline_metrics: dict, bestshot_metrics: dict,
                 baseline_spec: ModelSpec, bestshot_spec: ModelSpec, repeats: int) -> DeltaRow:
    """Compute one model's tuning delta, PAIRED on every axis over the completed-in-both intersection,
    with a McNemar verdict, a descriptive CI-overlap, and the full noise/confound/causal flag set
    (sub-project D D6). Crash-safe: a zero-task phase short-circuits to no_data with no numeric delta,
    and an empty task intersection leaves each paired axis None (never statistics.median([])) rather
    than fabricating a within-noise change. See DeltaRow for the field contract."""
    row = DeltaRow(model_key=bestshot.model_key)

    # Spec-derived + corpus-wide-aggregate flags (valid even when a phase produced no completed tasks).
    row.tuned_temperature = bestshot_spec.temperature
    row.stochastic_bestshot = (bestshot_spec.temperature not in (None, 0)) and repeats == 1
    row.sampling_uncontrolled = bool(baseline_spec.omit_temp or bestshot_spec.omit_temp
                                     or baseline_spec.reasoning or bestshot_spec.reasoning)
    row.baseline_gateway, row.bestshot_gateway = baseline.gateway, bestshot.gateway
    row.gateway_changed = baseline.gateway != bestshot.gateway
    row.baseline_n_operational = baseline.n_operational
    row.bestshot_n_operational = bestshot.n_operational
    row.n_operational_delta = bestshot.n_operational - baseline.n_operational
    row.baseline_p50_s, row.bestshot_p50_s = baseline.p50_latency_s, bestshot.p50_latency_s
    row.baseline_mean_elegance, row.bestshot_mean_elegance = baseline.mean_elegance, bestshot.mean_elegance
    row.baseline_n_elegance_judged = baseline.n_elegance_judged
    row.bestshot_n_elegance_judged = bestshot.n_elegance_judged
    row.baseline_cost_proxy_per_task_usd = baseline.cost_proxy_per_task_usd
    row.bestshot_cost_proxy_per_task_usd = bestshot.cost_proxy_per_task_usd
    row.cost_usd_delta = bestshot.cost_per_task_usd - baseline.cost_per_task_usd
    row.n_tasks_mismatch = baseline.n_tasks != bestshot.n_tasks
    row.task_composition_mismatch = set(baseline_metrics) != set(bestshot_metrics)

    same_gw = baseline.gateway == bestshot.gateway
    row.gateway_capped = bool(same_gw and baseline.n_operational > 0
                              and bestshot.n_operational >= baseline.n_operational)
    row.tuning_induced_regression = bool(same_gw and baseline.n_operational == 0
                                         and bestshot.n_operational > 0)
    # Order-confound (gateway-agnostic, None-p50 safe): a reliability swing OR a >2x/<0.5x p50 shift.
    ocs = row.n_operational_delta != 0
    if (baseline.p50_latency_s is not None and bestshot.p50_latency_s is not None
            and baseline.p50_latency_s > 0):
        ratio = bestshot.p50_latency_s / baseline.p50_latency_s
        if ratio < 0.5 or ratio > 2.0:
            ocs = True
    row.order_confound_suspect = ocs

    # Zero-task guard (D6d): never fabricate a delta or a degenerate wilson_ci(0,0).
    if baseline.n_tasks == 0 or bestshot.n_tasks == 0:
        row.no_data = True
        row.no_data_reason = "a phase produced zero completed tasks"
        row.notes.append("no paired delta: " + row.no_data_reason)
        return row

    # Descriptive secondary: do the phase-level (corpus-wide) Wilson CIs overlap? Mirrors rank._overlap
    # but recomputes the CIs here so the flag never depends on assemble() having mutated the aggregates.
    bl_lo, bl_hi = wilson_ci(baseline.n_passed, baseline.n_tasks)
    bs_lo, bs_hi = wilson_ci(bestshot.n_passed, bestshot.n_tasks)
    row.ci_overlap = bl_lo <= bs_hi and bs_lo <= bl_hi

    inter = set(baseline_metrics) & set(bestshot_metrics)
    row.baseline_completed = len(baseline_metrics)
    row.bestshot_completed = len(bestshot_metrics)
    row.completed_delta = row.bestshot_completed - row.baseline_completed
    row.empty_intersection = not inter

    # PRIMARY accuracy: exact McNemar over the intersection (paired_significance intersects internally).
    row.paired = paired_significance({t: m.passed for t, m in baseline_metrics.items()},
                                     {t: m.passed for t, m in bestshot_metrics.items()})

    if inter:
        row.baseline_pass_fraction = sum(baseline_metrics[t].passed for t in inter) / len(inter)
        row.bestshot_pass_fraction = sum(bestshot_metrics[t].passed for t in inter) / len(inter)
        row.pass_fraction_delta = row.bestshot_pass_fraction - row.baseline_pass_fraction
    else:
        row.notes.append("empty task-composition intersection: paired axes are None despite both "
                         "phases completing tasks (asymmetric operational/budget truncation)")

    # Speed: MEDIAN OF PER-TASK DIFFERENCES (median is non-linear, so only per-task diffs are paired).
    lat_diffs = [bestshot_metrics[t].latency_s - baseline_metrics[t].latency_s for t in inter
                 if baseline_metrics[t].latency_s is not None and bestshot_metrics[t].latency_s is not None]
    row.p50_paired_delta = statistics.median(lat_diffs) if lat_diffs else None

    # Cost proxy: mean of per-task diffs (mean is linear, so per-task-diff == diff-of-means).
    cost_diffs = [bestshot_metrics[t].cost_proxy_usd - baseline_metrics[t].cost_proxy_usd for t in inter]
    row.cost_proxy_paired_delta = statistics.mean(cost_diffs) if cost_diffs else None

    # Elegance: mean of per-task diffs over tasks judged in BOTH phases.
    eleg_diffs = [bestshot_metrics[t].elegance - baseline_metrics[t].elegance for t in inter
                  if baseline_metrics[t].elegance is not None and bestshot_metrics[t].elegance is not None]
    row.elegance_paired_delta = statistics.mean(eleg_diffs) if eleg_diffs else None
    return row


def detect_contamination(pass_counts: dict[str, int],
                         attempted_counts: dict[str, int]) -> list[str]:
    """Flag tasks a suspiciously large share of models passed perfectly (SPEC §4).

    A task is flagged when at least ``max(2, floor(0.75 * n_attempted))`` models passed it
    perfectly, where ``n_attempted`` is per task: the number of models with at least one
    successful (non-error) run on THAT task. A task with fewer than 2 attempters cannot be
    judged and is never flagged. Returned sorted for stable reports.
    """
    flagged = []
    for tid, attempted in attempted_counts.items():
        if attempted < 2:
            continue
        threshold = max(2, floor(0.75 * attempted))
        if pass_counts.get(tid, 0) >= threshold:
            flagged.append(tid)
    return sorted(flagged)


def _strongest_key(a: ModelAggregate):
    # REPORT ordering only (report_rows + the scoreboard). The ladder is a SEPARATE sort in
    # assemble() and deliberately does NOT consider elegance (SPEC §2 PL1). Elegance breaks a
    # pass-fraction tie here; unjudged (None) sorts as -1.0 so a judged model outranks an
    # unjudged one on an otherwise-equal tie, and a run with uniform None is unchanged.
    p50 = a.p50_latency_s if a.p50_latency_s is not None else float("inf")
    eleg = a.mean_elegance if a.mean_elegance is not None else -1.0
    return (-a.pass_fraction, -eleg, a.cost_per_task_usd, p50, a.model_key)


def _overlap(a: ModelAggregate, b: ModelAggregate) -> bool:
    return a.ci_low <= b.ci_high and b.ci_low <= a.ci_high


def gateway_reliability(aggregates: Iterable[ModelAggregate]) -> dict:
    """Roll operational (provider) failures up by gateway (sub-project B). Covers scored task
    calls only, not warm-up or judge calls. Aggregates with gateway None are skipped."""
    out: dict = {}
    for a in aggregates:
        if a.gateway is None:
            continue
        g = out.setdefault(a.gateway, {"attempts": 0, "operational": 0})
        g["attempts"] += a.n_tasks
        g["operational"] += a.n_operational
    for g in out.values():
        g["failure_rate"] = (g["operational"] / g["attempts"]) if g["attempts"] else 0.0
    return out


def assemble(
    aggregates: Iterable[ModelAggregate],
    ceiling_key: Optional[str] = None,
    bar: float = 0.0,
) -> LadderResult:
    rows = list(aggregates)
    for a in rows:
        a.ci_low, a.ci_high = wilson_ci(a.n_passed, a.n_tasks)

    reasoning = sorted([a for a in rows if a.reasoning], key=_strongest_key)
    non = sorted([a for a in rows if not a.reasoning], key=_strongest_key)
    report_rows = reasoning + non

    pairs: list[tuple[str, str]] = []
    for group in (reasoning, non):
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                if _overlap(group[i], group[j]):
                    pairs.append((group[i].model_key, group[j].model_key))

    # Weakest-first ladder; ceiling pinned last regardless of its score (SPEC §2 PL1).
    candidates = [a for a in rows if a.model_key != ceiling_key and a.pass_fraction >= bar]
    candidates.sort(key=lambda a: (a.pass_fraction, a.cost_per_task_usd, a.model_key))
    ladder = [a.model_key for a in candidates]
    if ceiling_key:
        ladder = [k for k in ladder if k != ceiling_key] + [ceiling_key]

    excluded = sorted(a.model_key for a in rows
                      if a.model_key != ceiling_key and a.pass_fraction < bar)

    by_key = {a.model_key: a for a in rows}
    gr = gateway_reliability(rows)

    notes = [
        "Reasoning and non-reasoning models are ranked in separate groups; do not compare across groups.",
        "Ladder is weakest-first (pass_fraction ascending).",
    ]
    if ceiling_key:
        notes.append(f"{ceiling_key} pinned last as the declared ceiling.")
    if excluded:
        # Annotate an exclusion that was operational (provider) rather than a genuine low score, so a
        # reader scanning "who got cut" is not misled (ladder.yaml itself carries only bare keys).
        parts = [f"{k} ({by_key[k].n_operational}/{by_key[k].n_tasks} operational, not wrong answers)"
                 if by_key[k].n_operational else k for k in excluded]
        notes.append(f"{len(excluded)} model(s) excluded from the ladder "
                     f"(pass_fraction < {bar:.2f}): {', '.join(parts)}.")
    if len(ladder) <= 1:
        notes.append("WARNING: the ladder has <= 1 entry after bar exclusions; the quality "
                     "gate has no escalation path below the ceiling.")
    if pairs:
        notes.append(f"{len(pairs)} pair(s) statistically indistinguishable (overlapping 95% CIs).")

    # Reliability divergence (sub-project B): flag every model whose raw and completed pass fractions
    # differ because of operational (provider) failures. The ladder still uses raw pass_fraction;
    # whether the accuracy axis switches to completed accuracy is a separate, unscheduled behaviour change.
    for a in rows:
        if a.n_operational:
            compl = f"{a.completed_pass_fraction:.0%}" if a.completed_pass_fraction is not None else "n/a"
            notes.append(f"RELIABILITY: {a.model_key} had {a.n_operational}/{a.n_tasks} operational "
                         f"(provider) failures; raw pass {a.pass_fraction:.0%} vs completed {compl}; "
                         f"the ladder still uses raw pass_fraction (re-basing is a separate decision).")
    if gr:
        notes.append("Per-gateway reliability counts scored task calls only, not warm-up or judge calls.")

    return LadderResult(
        report_rows=report_rows,
        ladder=ladder,
        indistinguishable_pairs=pairs,
        notes=notes,
        gateway_reliability=gr,
    )
