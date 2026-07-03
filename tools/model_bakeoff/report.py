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


# --- Sub-project D: dual-run (baseline vs best-shot) report + summary -------------------------------

# Models with an actual vendor citation for thinking-mode sampling non-determinism
# (coding-inference-settings.md). The sampling_uncontrolled caveat is rendered in TWO TIERS: a
# "vendor-documented" wording for these, a "heuristic caution, undocumented" wording for every other
# omit_temp/reasoning model, so publication-facing prose never overclaims the evidentiary basis.
CITED_SAMPLING_MODELS = frozenset({"deepseek-v4-pro", "deepseek-v4-flash"})


def _min_discordant_for_significance(alpha: float = 0.05) -> int:
    """Fewest all-one-directional discordant pairs for an exact two-sided McNemar p < alpha
    (p = 2 * 0.5^k when all k discordant flips go one way)."""
    k = 1
    while 2.0 * 0.5 ** k >= alpha:
        k += 1
    return k


def _dr_s(x) -> str:
    return f"{x:.2f}s" if x is not None else "n/a"


def _dr_signed(x, pct: bool = False, suffix: str = "") -> str:
    if x is None:
        return "n/a"
    return f"{x:+.0%}{suffix}" if pct else f"{x:+.4f}{suffix}"


def _sampling_caveat(model_key: str) -> str:
    if model_key in CITED_SAMPLING_MODELS:
        return ("sampling_uncontrolled: sampling is server-controlled and VENDOR-DOCUMENTED "
                f"non-deterministic in thinking mode for {model_key} (see coding-inference-settings.md); "
                "a single-draw flip may be pure vendor-internal sampling noise. Recommend --repeats>=3.")
    return ("sampling_uncontrolled: reasoning-model sampling is commonly server-controlled and is "
            f"UNDOCUMENTED for {model_key}; treat as a heuristic caution, not a confirmed vendor fact. "
            "Recommend --repeats>=3.")


def _dualrun_flags(row) -> list[str]:
    """The per-row causal + noise + confound annotations, most-serious first."""
    flags = []
    if row.low_confidence:
        reasons = ", ".join(row.low_confidence_reasons) or "unspecified"
        flags.append(f"tuned settings LOW-CONFIDENCE ({reasons}); treat this delta as indicative")
    if row.no_data:
        flags.append(f"NO DATA: {row.no_data_reason}")
    if row.gateway_capped:
        flags.append("gateway_capped: reliability gap persists on the SAME gateway; tuned settings "
                     "cannot fix a gateway ceiling")
    if row.tuning_induced_regression:
        flags.append("tuning_induced_regression: tuned settings INTRODUCED operational failures "
                     "(regression), not a pre-existing ceiling")
    if row.order_confound_suspect:
        extra = (f" (gateway changed {row.baseline_gateway} to {row.bestshot_gateway})"
                 if row.gateway_changed else "")
        flags.append("order_confound_suspect: a large reliability or latency swing between phases" + extra
                     + "; with no within-model order repetition this cannot fully separate an ordering "
                     "artefact from a real effect")
    if row.stochastic_bestshot:
        flags.append(f"stochastic_bestshot: best-shot enables sampling temperature={row.tuned_temperature}; "
                     "a single-draw flip may be sampling noise. Recommend --repeats>=3.")
    if row.sampling_uncontrolled:
        flags.append(_sampling_caveat(row.model_key))
    if row.task_composition_mismatch:
        flags.append("task_composition_mismatch: the phases completed different task sets; the "
                     "non-accuracy deltas are over their intersection only")
    if row.n_tasks_mismatch:
        flags.append("n_tasks_mismatch: the phases attempted different task counts")
    if row.empty_intersection:
        flags.append("empty_intersection: no task completed in BOTH phases; the paired axes are None")
    return flags


def _dualrun_elegance_line(row, elegance_policy: str, n_tasks: int) -> str:
    if elegance_policy == "none":
        return "- elegance: not judged (--elegance none)"
    if elegance_policy == "bestshot":
        best = f"{row.bestshot_mean_elegance:.3f}" if row.bestshot_mean_elegance is not None else "n/a"
        return (f"- elegance: baseline unjudged (--elegance bestshot policy); best-shot mean {best} "
                f"(n={row.bestshot_n_elegance_judged})")
    # policy == "both"
    if row.elegance_paired_delta is not None:
        return (f"- elegance (paired diff over jointly-judged tasks): "
                f"{_dr_signed(row.elegance_paired_delta)}")
    return (f"- elegance: partial judge coverage (baseline n={row.baseline_n_elegance_judged}, "
            f"best-shot n={row.bestshot_n_elegance_judged} of {n_tasks}; shared budget likely "
            "exhausted by this point in the run order)")


def _dualrun_model_block(row, elegance_policy: str, n_tasks: int, orders) -> list[str]:
    out = [f"### {row.model_key}"]
    if orders and row.model_key in orders:
        out.append(f"- phase order: {orders[row.model_key]}")
    p = row.paired
    if row.no_data:
        out.append(f"- NO DATA: {row.no_data_reason}; no paired delta computed for this model")
    elif p is None:
        out.append("- accuracy (paired McNemar, PRIMARY): n/a (no paired result available)")
    else:
        sig = "significant" if p.significant else "not significant"
        out.append(f"- accuracy (paired McNemar, PRIMARY): {p.n_paired} paired, {p.b} regressed / "
                   f"{p.c} improved, p={p.p_value:.4f} ({sig} at n={p.n_paired})")
        if row.pass_fraction_delta is not None:
            out.append(f"- pass fraction (paired intersection): {row.baseline_pass_fraction:.0%} to "
                       f"{row.bestshot_pass_fraction:.0%} (delta {_dr_signed(row.pass_fraction_delta, pct=True)})")
        else:
            out.append("- pass fraction (paired intersection): n/a (empty task-composition intersection)")
        overlap = "overlap (indistinguishable)" if row.ci_overlap else "no overlap"
        line = f"- CI-overlap (descriptive secondary): {overlap}"
        if row.ci_overlap == p.significant:
            line += (f"; NOTE: CI-overlap and the paired test disagree at n={p.n_paired}; the paired "
                     "McNemar p-value is authoritative")
        out.append(line)
    out.append(f"- reliability (corpus-wide operational): {row.baseline_n_operational} to "
               f"{row.bestshot_n_operational} (delta {row.n_operational_delta:+d})")
    out.append(f"- speed (paired median of per-task diffs): {_dr_signed(row.p50_paired_delta, suffix='s')}"
               f"  |  phase p50 (corpus-wide, context only): base {_dr_s(row.baseline_p50_s)} best "
               f"{_dr_s(row.bestshot_p50_s)}")
    out.append(f"- cost proxy (paired mean of per-task diffs): {_dr_signed(row.cost_proxy_paired_delta)}"
               f"  |  phase proxy/task (context only): base {row.baseline_cost_proxy_per_task_usd:.4f} "
               f"best {row.bestshot_cost_proxy_per_task_usd:.4f}")
    if row.cost_usd_delta:
        out.append(f"- real cost/task delta (metered only): ${row.cost_usd_delta:+.4f}")
    out.append(_dualrun_elegance_line(row, elegance_policy, n_tasks))
    flags = _dualrun_flags(row)
    if flags:
        out.append("- flags:")
        out += [f"  - {f}" for f in flags]
    return out


def render_dualrun_md(rows, *, run_id: str = "", suite_selector: Optional[str] = None,
                      n_tasks: int = 0, elegance_policy: str = "bestshot", order: str = "alternate",
                      provenance: Optional[dict] = None, drift_notes: Optional[list] = None,
                      run_notes: Optional[list] = None, orders: Optional[dict] = None,
                      n_tuned_records: Optional[int] = None) -> str:
    """Human-readable dual-run report (sub-project D). Accuracy is the PRIMARY paired-McNemar verdict
    with raw n_paired/b/c; CI-overlap is a labelled descriptive secondary; the non-accuracy deltas are
    paired over the completed-in-both intersection with corpus-wide phase aggregates marked context-only;
    and every noise/confound/causal flag plus the leakage/provenance, drift, low-confidence, and
    corpus-power/multiplicity caveats are surfaced. House style: no em/en-dashes; ranges written 'a to b'."""
    rows = list(rows)
    out: list[str] = ["# Model bakeoff dual-run report (baseline vs best-shot tuning delta)", ""]
    if run_id:
        out.append(f"Run: `{run_id}`")
    out.append(f"Suite: {suite_selector or 'whole corpus'} ({n_tasks} task(s)).")

    # Run-level low-confidence banner (D7): >50% of models with tuned records flagged low-confidence.
    denom = n_tuned_records if n_tuned_records is not None else len(rows)
    low = [r.model_key for r in rows if r.low_confidence]
    if denom and len(low) / denom > 0.5:
        out += ["", f"> WARNING: tuned settings are LOW-CONFIDENCE for {len(low)} of {denom} models "
                f"({', '.join(low)}); treat all deltas as indicative, not conclusive."]

    out += ["", "## Leakage and provenance"]
    prov = provenance or {}
    out.append(f"- scored corpus dir: {prov.get('scored_dir', 'n/a')}")
    out.append(f"- dev corpus dir: {prov.get('dev_dir') or '(absent)'}")
    if prov.get("leakage_checked"):
        out.append("- leakage check: disjoint (verified); scored and dev corpora share no task_id")
    else:
        out.append("- leakage check: NOT CHECKED (dev corpus and id list both absent; not blocking, "
                   "but leakage could not be attested)")
    dc = prov.get("dev_corpus") or {}
    if dc:
        out.append(f"- provenance: dev_corpus tree_sha={dc.get('tree_sha', 'n/a')}, "
                   f"oracle_ref_sha256={dc.get('oracle_ref_sha256', 'n/a')}, "
                   f"dev_tasks={len(dc.get('dev_tasks', []))}")

    out += ["", "## Tuned-spec reconstruction notes"]
    if drift_notes:
        out += [f"- {n}" for n in drift_notes]
    else:
        out.append("- all selected models reconstructed their tuned spec cleanly (no drift/fallback)")

    out += ["", "## Per-model tuning delta"]
    if not rows:
        out.append("- no models produced a delta row")
    for r in rows:
        out += [""] + _dualrun_model_block(r, elegance_policy, n_tasks, orders)

    k = _min_discordant_for_significance()
    n_tuned = denom
    out += ["", "## Methodology and caveats",
            f"- The corpus is small ({n_tasks} task(s)); most per-model deltas are statistically "
            "indistinguishable. Read the paired McNemar p-value, not the point delta.",
            f"- At least {k} all-one-directional discordant pairs are required for exact-McNemar "
            "significance at p<0.05 (fewer flips can never reach it, whatever the point delta looks like).",
            "- Cost proxy = token-volume x sticker price, a USD-equivalent comparison figure, NOT real "
            "spend (all subscription models bill $0 marginal).",
            f"- Phase order policy: {order}. `alternate` counterbalancing protects the FLEET aggregate, "
            "not an individual model's back-to-back pair (that is order_confound_suspect).",
            f"- N_tuned_models={n_tuned}; do not vote-count improved/regressed rows across models as "
            "evidence of a general tuning effect without accounting for per-row and cross-model "
            "multiplicity."]

    if run_notes:
        out += ["", "## Notes"] + [f"- {n}" for n in run_notes]
    return "\n".join(out) + "\n"


def render_dualrun_summary(rows, *, phase_metrics: Optional[dict] = None, run_id: str = "",
                           suite_selector: Optional[str] = None, n_tasks: int = 0,
                           elegance_policy: str = "bestshot", order: str = "alternate",
                           provenance: Optional[dict] = None, run_notes: Optional[list] = None,
                           orders: Optional[dict] = None, n_tuned_records: Optional[int] = None) -> dict:
    """Machine-readable dual-run rollup: per-model deltas + paired stats + descriptive CI-overlap +
    per-task pass-rates (D6g flakiness) + all flags + provenance + order + notes."""
    phase_metrics = phase_metrics or {}
    orders = orders or {}
    models = []
    for r in rows:
        pm = phase_metrics.get(r.model_key, {})
        models.append({
            "model_key": r.model_key,
            "no_data": r.no_data, "no_data_reason": r.no_data_reason,
            "paired": ({"n_paired": r.paired.n_paired, "b": r.paired.b, "c": r.paired.c,
                        "p_value": r.paired.p_value, "significant": r.paired.significant}
                       if r.paired else None),
            "baseline_pass_fraction": r.baseline_pass_fraction,
            "bestshot_pass_fraction": r.bestshot_pass_fraction,
            "pass_fraction_delta": r.pass_fraction_delta,
            "completed_delta": r.completed_delta,
            "n_operational_delta": r.n_operational_delta,
            "p50_paired_delta_s": r.p50_paired_delta,
            "cost_proxy_paired_delta_usd": r.cost_proxy_paired_delta,
            "cost_usd_delta": r.cost_usd_delta,
            "elegance_paired_delta": r.elegance_paired_delta,
            "baseline_n_elegance_judged": r.baseline_n_elegance_judged,
            "bestshot_n_elegance_judged": r.bestshot_n_elegance_judged,
            "ci_overlap": r.ci_overlap,
            "task_composition_mismatch": r.task_composition_mismatch,
            "n_tasks_mismatch": r.n_tasks_mismatch,
            "empty_intersection": r.empty_intersection,
            "gateway_capped": r.gateway_capped,
            "tuning_induced_regression": r.tuning_induced_regression,
            "order_confound_suspect": r.order_confound_suspect,
            "stochastic_bestshot": r.stochastic_bestshot,
            "sampling_uncontrolled": r.sampling_uncontrolled,
            "tuned_temperature": r.tuned_temperature,
            "low_confidence": r.low_confidence,
            "low_confidence_reasons": list(r.low_confidence_reasons),
            "order": orders.get(r.model_key),
            # D6g flakiness: per-task pass-rate for each phase, so E can report a 1/3 to 3/3 shift
            # even when the binary verdict is unmoved.
            "baseline_pass_rates": {t: m.pass_rate for t, m in pm.get("baseline", {}).items()},
            "bestshot_pass_rates": {t: m.pass_rate for t, m in pm.get("bestshot", {}).items()},
        })
    return {
        "run_id": run_id, "suite": suite_selector, "n_tasks": n_tasks,
        "elegance_policy": elegance_policy, "order": order,
        "n_tuned_records": (n_tuned_records if n_tuned_records is not None else len(list(rows))),
        "provenance": provenance, "notes": run_notes or [], "models": models,
    }


def render_dualrun_summary_json(rows, **kwargs) -> str:
    return json.dumps(render_dualrun_summary(rows, **kwargs), indent=2) + "\n"
