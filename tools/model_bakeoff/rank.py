"""Ranking and ladder assembly (SPEC §2, §9). Pure given the aggregates.

Report ordering: pass_fraction desc, cost_per_task asc, p50 latency asc, name,
computed WITHIN a reasoning group (reasoning and non-reasoning are never merged).
Ladder ordering: weakest-first (pass_fraction asc) with the declared ceiling
pinned last, matching what load_ladder()/next_rung() in the quality-gate expect.
"""
from __future__ import annotations

from math import floor
from typing import Iterable, Optional

from .models import LadderResult, ModelAggregate


def wilson_ci(passed: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score 95% interval on a pass fraction (SPEC §8/§9)."""
    if n <= 0:
        return (0.0, 0.0)
    p = passed / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


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
    p50 = a.p50_latency_s if a.p50_latency_s is not None else float("inf")
    return (-a.pass_fraction, a.cost_per_task_usd, p50, a.model_key)


def _overlap(a: ModelAggregate, b: ModelAggregate) -> bool:
    return a.ci_low <= b.ci_high and b.ci_low <= a.ci_high


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

    notes = [
        "Reasoning and non-reasoning models are ranked in separate groups; do not compare across groups.",
        "Ladder is weakest-first (pass_fraction ascending).",
    ]
    if ceiling_key:
        notes.append(f"{ceiling_key} pinned last as the declared ceiling.")
    if excluded:
        notes.append(f"{len(excluded)} model(s) excluded from the ladder "
                     f"(pass_fraction < {bar:.2f}): {', '.join(excluded)}.")
    if len(ladder) <= 1:
        notes.append("WARNING: the ladder has <= 1 entry after bar exclusions; the quality "
                     "gate has no escalation path below the ceiling.")
    if pairs:
        notes.append(f"{len(pairs)} pair(s) statistically indistinguishable (overlapping 95% CIs).")

    return LadderResult(
        report_rows=report_rows,
        ladder=ladder,
        indistinguishable_pairs=pairs,
        notes=notes,
    )
