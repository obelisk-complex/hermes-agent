"""Render bakeoff results to durable artefacts (SPEC §9):
- report.md   : human-readable, reasoning/non-reasoning kept in separate groups,
                pass fraction with Wilson CI, cost, p50 latency, ladder, notes.
- ladder.yaml : drop-in quality_gate.model_ladder (weakest first, ceiling last).
- summary.json: machine-readable rollup.

House style: no em/en-dashes; ranges written 'a to b'.
"""
from __future__ import annotations

import json
from typing import Optional

from . import rank
from .models import ERR_TEST_FAIL, OPERATIONAL_ERROR_TYPES, LadderResult


def _pct_or_na(x) -> str:
    return f"{x:.0%}" if x is not None else "n/a"


def render_ladder_yaml(ladder: list[str]) -> str:
    lines = ["quality_gate:", "  model_ladder:"]
    lines += [f"    - {m}" for m in ladder]
    return "\n".join(lines) + "\n"


def _row(a) -> str:
    ci = f"{a.ci_low:.2f} to {a.ci_high:.2f}"
    p50 = f"{a.p50_latency_s:.2f}s" if a.p50_latency_s is not None else "n/a"
    cost = "0 (sub)" if a.cost_model == "subscription" else f"${a.cost_per_task_usd:.4f}"
    return f"| {a.model_key} | {a.n_passed}/{a.n_tasks} ({a.pass_fraction:.0%}) | {ci} | {cost} | {p50} |"


def _elegance_cell(a) -> str:
    if a.mean_elegance is not None:
        return f"{a.mean_elegance:.2f} (n={a.n_elegance_judged})"
    return f"n/a (n={a.n_elegance_judged})"   # n/a distinguishes 'unjudged' from a real 0.0 score


def _scoreboard_row(i: int, a) -> str:
    ci = f"{a.ci_low:.2f} to {a.ci_high:.2f}"
    passci = f"{a.n_passed}/{a.n_tasks} ({a.pass_fraction:.0%}) CI {ci}"
    reasoning = "yes" if a.reasoning else "no"
    p50 = f"{a.p50_latency_s:.2f}s" if a.p50_latency_s is not None else "n/a"
    proxy = f"${a.cost_proxy_per_task_usd:.4f}"
    opfail = str(a.n_operational)
    if a.n_operational:   # show completed accuracy when provider failures skew the raw pass
        opfail += f" (compl {_pct_or_na(a.completed_pass_fraction)})"
    return (f"| {i} | {a.model_key} | {reasoning} | {passci} | "
            f"{_elegance_cell(a)} | {p50} | {proxy} | {opfail} |")


def _reliability_section(result) -> list[str]:
    """Sub-project B: per-gateway operational-failure table + per-model failure attribution.
    Only models with an operational OR a settings-artefact failure appear here; pure wrong
    answers (test_failure only) belong in the scoreboard, not this section."""
    out = ["", "## Reliability and error attribution"]
    gr = result.gateway_reliability
    if gr:
        out += ["", "| Gateway | Attempts | Op-fail | Rate |", "|---|---|---|---|"]
        for g in sorted(gr):
            v = gr[g]
            out.append(f"| {g} | {v['attempts']} | {v['operational']} | {v['failure_rate']:.0%} |")
        out.append("Per-gateway reliability counts scored task calls only, not warm-up or judge calls.")

    def _notable(a) -> bool:
        has_op = any(k in OPERATIONAL_ERROR_TYPES for k in a.error_counts)
        has_settings = any(k not in OPERATIONAL_ERROR_TYPES and k != ERR_TEST_FAIL for k in a.error_counts)
        return has_op or has_settings

    flagged = [a for a in result.report_rows if _notable(a)]
    if not flagged:
        out += ["", "- no operational or settings-attributable failures recorded"]
        return out
    out += ["", "Per-model failures (operational = provider fault; model/settings = the model's "
            "output or our token/sandbox budget, see sub-project C for truncation/timeout tuning):"]
    for a in flagged:
        op = {k: v for k, v in a.error_counts.items() if k in OPERATIONAL_ERROR_TYPES}
        other = {k: v for k, v in a.error_counts.items() if k not in OPERATIONAL_ERROR_TYPES}
        out.append(f"- {a.model_key}: raw pass {a.pass_fraction:.0%}, completed "
                   f"{_pct_or_na(a.completed_pass_fraction)}; operational {op or dict()}; "
                   f"model/settings {other or dict()}")
    return out


def render_report_md(result: LadderResult, run_id: str = "", n_tasks: int = 0,
                     suite_selector: Optional[str] = None) -> str:
    out: list[str] = ["# Model bakeoff report", ""]
    if run_id:
        out.append(f"Run: `{run_id}`")
    out.append(f"Corpus: {n_tasks} task(s). Indicative, not authoritative (SPEC §13).")
    out.append(f"Suite: {suite_selector or 'whole corpus'} ({n_tasks} task(s)).")

    # PRIMARY view (A3): one 4-axis scoreboard across ALL models, globally strongest-first.
    scoreboard = sorted(result.report_rows, key=rank._strongest_key)
    if scoreboard:
        out += ["", "## Scoreboard (all models, strongest first)", "",
                "| Rank | Model | Reasoning | Pass (95% CI) | Elegance (n) | p50 | Cost proxy/task | Op-fail |",
                "|---|---|---|---|---|---|---|---|"]
        out += [_scoreboard_row(i, a) for i, a in enumerate(scoreboard, 1)]
        out += ["", "Note: the per-group tables below rank within the reasoning / non-reasoning "
                "split only; do NOT compare pass fractions across the two groups (SPEC §2). Cost "
                "proxy prices output tokens at each model's sticker rate (subscription marginal "
                "cost is 0). Op-fail counts operational (provider) failures; see the Reliability "
                "section for how they split from wrong answers."]

    out += _reliability_section(result)

    reasoning = [a for a in result.report_rows if a.reasoning]
    non = [a for a in result.report_rows if not a.reasoning]
    for title, group in (("Reasoning models", reasoning), ("Non-reasoning models", non)):
        if not group:
            continue
        out += ["", f"## {title} (strongest first)", "",
                "| Model | Pass | 95% CI | Cost/task | p50 |", "|---|---|---|---|---|"]
        out += [_row(a) for a in group]

    out += ["", "## Proposed quality_gate ladder (weakest first)", "", "```yaml",
            render_ladder_yaml(result.ladder).rstrip(), "```"]

    if result.indistinguishable_pairs:
        out += ["", "## Statistically indistinguishable (overlapping 95% CIs)"]
        out += [f"- {a} vs {b}" for a, b in result.indistinguishable_pairs]

    out += ["", "## Contamination flags"]
    if result.contamination_flags:
        out += [f"- {tid}" for tid in result.contamination_flags]
    else:
        out += ["- none detected"]

    out += ["", "## Notes"] + [f"- {n}" for n in result.notes]
    return "\n".join(out) + "\n"


def render_summary(result: LadderResult, run_id: str = "", n_tasks: int = 0,
                   ping_baselines: Optional[dict] = None,
                   budget_spent: Optional[float] = None,
                   suite: Optional[dict] = None) -> dict:
    return {
        "run_id": run_id,
        "n_tasks": n_tasks,
        "suite": suite,        # {"selector": <str|null>, "task_ids": [...]}; None => whole corpus
        "ladder": result.ladder,
        "ping_baselines_s": ping_baselines or {},
        "budget_spent_usd": budget_spent,
        "gateway_reliability": result.gateway_reliability,   # {gateway: {attempts, operational, failure_rate}}
        "models": [
            {
                "key": a.model_key, "reasoning": a.reasoning, "cost_model": a.cost_model,
                "pass_fraction": a.pass_fraction, "n_passed": a.n_passed, "n_tasks": a.n_tasks,
                "ci_low": a.ci_low, "ci_high": a.ci_high,
                "cost_per_task_usd": a.cost_per_task_usd, "p50_latency_s": a.p50_latency_s,
                "n_latency_samples": a.n_latency_samples,
                "mean_elegance": a.mean_elegance, "n_elegance_judged": a.n_elegance_judged,
                "cost_proxy_per_task_usd": a.cost_proxy_per_task_usd,
                "gateway": a.gateway, "error_counts": a.error_counts,
                "n_operational": a.n_operational,
                "completed_pass_fraction": a.completed_pass_fraction,
            }
            for a in result.report_rows
        ],
        "indistinguishable_pairs": [list(p) for p in result.indistinguishable_pairs],
        "notes": result.notes,
        "contamination_flags": list(result.contamination_flags),
    }


def render_summary_json(result: LadderResult, **kwargs) -> str:
    return json.dumps(render_summary(result, **kwargs), indent=2) + "\n"
