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

from .models import LadderResult


def render_ladder_yaml(ladder: list[str]) -> str:
    lines = ["quality_gate:", "  model_ladder:"]
    lines += [f"    - {m}" for m in ladder]
    return "\n".join(lines) + "\n"


def _row(a) -> str:
    ci = f"{a.ci_low:.2f} to {a.ci_high:.2f}"
    p50 = f"{a.p50_latency_s:.2f}s" if a.p50_latency_s is not None else "n/a"
    cost = "0 (sub)" if a.cost_model == "subscription" else f"${a.cost_per_task_usd:.4f}"
    return f"| {a.model_key} | {a.n_passed}/{a.n_tasks} ({a.pass_fraction:.0%}) | {ci} | {cost} | {p50} |"


def render_report_md(result: LadderResult, run_id: str = "", n_tasks: int = 0) -> str:
    out: list[str] = ["# Model bakeoff report", ""]
    if run_id:
        out.append(f"Run: `{run_id}`")
    out.append(f"Corpus: {n_tasks} task(s). Indicative, not authoritative (SPEC §13).")

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
                   budget_spent: Optional[float] = None) -> dict:
    return {
        "run_id": run_id,
        "n_tasks": n_tasks,
        "ladder": result.ladder,
        "ping_baselines_s": ping_baselines or {},
        "budget_spent_usd": budget_spent,
        "models": [
            {
                "key": a.model_key, "reasoning": a.reasoning, "cost_model": a.cost_model,
                "pass_fraction": a.pass_fraction, "n_passed": a.n_passed, "n_tasks": a.n_tasks,
                "ci_low": a.ci_low, "ci_high": a.ci_high,
                "cost_per_task_usd": a.cost_per_task_usd, "p50_latency_s": a.p50_latency_s,
            }
            for a in result.report_rows
        ],
        "indistinguishable_pairs": [list(p) for p in result.indistinguishable_pairs],
        "notes": result.notes,
        "contamination_flags": list(result.contamination_flags),
    }


def render_summary_json(result: LadderResult, **kwargs) -> str:
    return json.dumps(render_summary(result, **kwargs), indent=2) + "\n"
