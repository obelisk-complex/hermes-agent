"""report rendering (SPEC §9). Offline, pure."""
from __future__ import annotations

import json

from tools.model_bakeoff import rank, report
from tools.model_bakeoff.models import ModelAggregate, TaskMetric
from tools.model_bakeoff.rank import DeltaRow, PairedResult


def agg(key, passed, n, reasoning=True, cost=0.0, cm="subscription", p50=1.0):
    return ModelAggregate(model_key=key, reasoning=reasoning, cost_model=cm,
                          n_tasks=n, n_passed=passed, cost_per_task_usd=cost, p50_latency_s=p50)


def _result():
    aggs = [agg("weak", 3, 10), agg("strong", 9, 10),
            agg("opus", 10, 10, reasoning=False, cm="metered", cost=0.02)]
    return rank.assemble(aggs, ceiling_key="opus")


def test_ladder_yaml_schema_and_ceiling_last():
    y = report.render_ladder_yaml(["weak", "strong", "opus"])
    assert "quality_gate:" in y
    assert "  model_ladder:" in y
    assert y.strip().endswith("- opus")
    assert y.index("- weak") < y.index("- strong") < y.index("- opus")


def test_report_md_has_groups_ladder_and_no_dashes():
    md = report.render_report_md(_result(), run_id="r1", n_tasks=10)
    assert "## Reasoning models" in md
    assert "## Non-reasoning models" in md
    assert "weakest first" in md
    assert "strong" in md and "weak" in md and "opus" in md
    # house style: no em/en dashes anywhere in a user-visible artefact
    assert "—" not in md and "–" not in md


def test_report_md_marks_subscription_cost_as_zero():
    md = report.render_report_md(_result(), n_tasks=10)
    assert "0 (sub)" in md


def test_summary_json_roundtrips_and_carries_ladder():
    s = report.render_summary_json(_result(), run_id="r1", n_tasks=10, budget_spent=0.0)
    data = json.loads(s)
    assert data["ladder"][-1] == "opus"
    assert data["n_tasks"] == 10
    assert any(m["key"] == "strong" for m in data["models"])


def test_report_md_renders_contamination_section_when_flagged():
    res = _result()
    res.contamination_flags = ["quick-rle"]
    md = report.render_report_md(res, n_tasks=10)
    assert "## Contamination flags" in md
    assert "- quick-rle" in md


def test_report_md_contamination_says_none_detected_when_empty():
    md = report.render_report_md(_result(), n_tasks=10)
    assert "## Contamination flags" in md
    assert "none detected" in md


def test_summary_json_carries_contamination_flags():
    res = _result()
    res.contamination_flags = ["t1"]
    data = json.loads(report.render_summary_json(res, run_id="r", n_tasks=10, budget_spent=0.0))
    assert data["contamination_flags"] == ["t1"]


# --- Task 7 / A3: primary 4-axis scoreboard + elegance/cost-proxy in summary ---

def _result_with_elegance():
    a = ModelAggregate(model_key="alpha", reasoning=True, cost_model="subscription",
                       n_tasks=10, n_passed=9, p50_latency_s=1.5,
                       mean_elegance=0.83, n_elegance_judged=7,
                       n_latency_samples=9, cost_proxy_per_task_usd=0.0012)
    b = ModelAggregate(model_key="beta", reasoning=False, cost_model="subscription",
                       n_tasks=10, n_passed=9, p50_latency_s=2.0,
                       mean_elegance=None, n_elegance_judged=0,
                       n_latency_samples=9, cost_proxy_per_task_usd=0.0)
    return rank.assemble([a, b], ceiling_key=None, bar=0.0)


def test_report_md_has_scoreboard_with_four_axes():
    md = report.render_report_md(_result_with_elegance(), run_id="r", n_tasks=10)
    assert "## Scoreboard" in md
    assert "Elegance" in md and "Cost proxy" in md
    assert "0.83 (n=7)" in md         # judged model shows score + sample count
    assert "n/a (n=0)" in md          # unjudged model shows n/a, not a fake 0.0
    assert "alpha" in md and "beta" in md
    assert "| yes |" in md and "| no |" in md   # reasoning column
    # the secondary group tables + their caveat remain
    assert "## Reasoning models" in md and "## Non-reasoning models" in md
    assert "—" not in md and "–" not in md      # house style


def test_summary_carries_elegance_cost_proxy_and_latency_count():
    data = json.loads(report.render_summary_json(
        _result_with_elegance(), run_id="r", n_tasks=10, budget_spent=0.0))
    m = {x["key"]: x for x in data["models"]}
    assert m["alpha"]["mean_elegance"] == 0.83
    assert m["alpha"]["n_elegance_judged"] == 7
    assert m["alpha"]["cost_proxy_per_task_usd"] == 0.0012
    assert m["alpha"]["n_latency_samples"] == 9
    assert m["beta"]["mean_elegance"] is None


# --- Sub-project B Task 3: reliability axis in report + summary ---

def _result_with_ops():
    a = ModelAggregate(model_key="alpha", reasoning=True, cost_model="subscription",
                       n_tasks=10, n_passed=6, gateway="opencode-go", n_operational=4,
                       error_counts={"call_error": 4})
    return rank.assemble([a])


def _result_all_operational():
    a = ModelAggregate(model_key="dead", reasoning=False, cost_model="subscription",
                       n_tasks=6, n_passed=0, gateway="opencode-go", n_operational=6,
                       error_counts={"call_error": 6})
    return rank.assemble([a])


def _result_truncation_only():   # flash-like: extraction_failed, NOT operational
    a = ModelAggregate(model_key="flashy", reasoning=True, cost_model="subscription",
                       n_tasks=10, n_passed=3, gateway="opencode-go", n_operational=0,
                       error_counts={"extraction_failed": 7})
    return rank.assemble([a])


def _result_pure_wrong():   # only test_failure: belongs in the scoreboard, not the reliability section
    a = ModelAggregate(model_key="purewrong", reasoning=False, cost_model="subscription",
                       n_tasks=10, n_passed=7, gateway="ollama-cloud", n_operational=0,
                       error_counts={"test_failure": 3})
    return rank.assemble([a])


def test_scoreboard_has_opfail_column():
    md = report.render_report_md(_result_with_ops(), run_id="r", n_tasks=10)
    assert "Op-fail" in md and "4" in md
    assert "—" not in md and "–" not in md


def test_all_operational_renders_na_not_crash():
    md = report.render_report_md(_result_all_operational(), run_id="r", n_tasks=6)
    assert "n/a" in md                                   # completed shown n/a, never a fake 0%
    data = json.loads(report.render_summary_json(_result_all_operational(), run_id="r", n_tasks=6))
    assert data["models"][0]["completed_pass_fraction"] is None


def test_reliability_section_and_gateway_table():
    md = report.render_report_md(_result_with_ops(), run_id="r", n_tasks=10)
    assert "## Reliability" in md and "opencode-go" in md and "call_error" in md


def test_truncation_only_model_is_visible_and_labelled():
    md = report.render_report_md(_result_truncation_only(), run_id="r", n_tasks=10)
    assert "flashy" in md and "extraction_failed" in md   # NOT hidden despite n_operational==0


def test_pure_wrong_answer_model_not_in_reliability_section():
    md = report.render_report_md(_result_pure_wrong(), run_id="r", n_tasks=10)
    assert "- purewrong:" not in md                       # pure wrong answers stay in the scoreboard


def test_summary_carries_reliability():
    data = json.loads(report.render_summary_json(_result_with_ops(), run_id="r", n_tasks=10))
    m = {x["key"]: x for x in data["models"]}["alpha"]
    assert m["n_operational"] == 4 and m["error_counts"] == {"call_error": 4}
    assert m["gateway"] == "opencode-go" and m["completed_pass_fraction"] == 6 / 6
    assert data["gateway_reliability"]["opencode-go"]["operational"] == 4


# --- Phase 1 Task 3: suite selector recorded in summary + report header ---

def test_summary_records_suite_payload():
    d = report.render_summary(_result(), suite={"selector": "tag:ai-trap", "task_ids": ["a", "b"]})
    assert d["suite"] == {"selector": "tag:ai-trap", "task_ids": ["a", "b"]}


def test_summary_suite_defaults_none():
    assert report.render_summary(_result())["suite"] is None


def test_report_md_shows_suite_line():
    md = report.render_report_md(_result(), run_id="r1", n_tasks=3, suite_selector="tag:ai-trap")
    assert "Suite: tag:ai-trap (3 task" in md


def test_report_md_suite_line_whole_corpus_by_default():
    md = report.render_report_md(_result(), n_tasks=10)
    assert "Suite: whole corpus" in md
    assert "—" not in md and "–" not in md      # house style


# --- Sub-project D Task 6: dual-run report + summary renderers ---

def _drow(key="m", **kw):
    row = DeltaRow(model_key=key)
    for k, v in kw.items():
        setattr(row, k, v)
    return row


def test_dualrun_md_primary_paired_stats_and_ci_disagreement():
    # p=0.0625 -> not significant; ci_overlap False while significant False -> they AGREE (both "same"),
    # so to force a disagreement we make ci_overlap True with significant False? that agrees too.
    # Disagreement fires when ci_overlap == significant. Here significant=False, ci_overlap=False -> agree.
    # Use a significant improvement with overlapping CIs to force disagreement.
    row = _drow("glm-5.2",
                paired=PairedResult(n_paired=8, b=0, c=6, p_value=0.03125, significant=True),
                baseline_pass_fraction=0.5, bestshot_pass_fraction=0.75, pass_fraction_delta=0.25,
                ci_overlap=True)
    md = report.render_dualrun_md([row], run_id="r", n_tasks=10)
    assert "accuracy (paired McNemar, PRIMARY): 8 paired, 0 regressed / 6 improved" in md
    assert "significant at n=8" in md
    assert "CI-overlap (descriptive secondary)" in md
    assert "the paired McNemar p-value is authoritative" in md   # D6h disagreement note
    assert "glm-5.2" in md
    assert "—" not in md and "–" not in md


def test_dualrun_md_min_discordant_fact_present():
    md = report.render_dualrun_md([], run_id="r", n_tasks=10)
    assert "6 all-one-directional discordant pairs are required" in md   # exact-McNemar floor at 0.05


def test_dualrun_md_elegance_baseline_unjudged_policy():
    row = _drow("m", bestshot_mean_elegance=0.82, bestshot_n_elegance_judged=7)
    md = report.render_dualrun_md([row], n_tasks=10, elegance_policy="bestshot")
    assert "baseline unjudged (--elegance bestshot policy)" in md
    assert "best-shot mean 0.820 (n=7)" in md


def test_dualrun_md_elegance_partial_coverage_under_both():
    # both-policy but the paired delta is None (partial coverage) -> the partial-coverage wording
    row = _drow("m", elegance_paired_delta=None, baseline_n_elegance_judged=1,
                bestshot_n_elegance_judged=2)
    md = report.render_dualrun_md([row], n_tasks=10, elegance_policy="both")
    assert "partial judge coverage" in md


def test_dualrun_md_cost_proxy_caption_and_context_labels():
    row = _drow("m", paired=PairedResult(2, 0, 0, 1.0, False), cost_proxy_paired_delta=0.0001,
                baseline_p50_s=1.0, bestshot_p50_s=1.2)
    md = report.render_dualrun_md([row], n_tasks=10)
    assert "Cost proxy = token-volume x sticker price" in md and "NOT real spend" in md
    assert "context only" in md                              # corpus-wide phase aggregates labelled


def test_dualrun_md_two_tier_sampling_caveat():
    cited = _drow("deepseek-v4-flash", paired=PairedResult(1, 0, 0, 1.0, False),
                  sampling_uncontrolled=True)
    md_c = report.render_dualrun_md([cited], n_tasks=10)
    assert "VENDOR-DOCUMENTED" in md_c and "coding-inference-settings.md" in md_c
    assert "--repeats>=3" in md_c
    noncited = _drow("kimi-k2.6", paired=PairedResult(1, 0, 0, 1.0, False), sampling_uncontrolled=True)
    md_n = report.render_dualrun_md([noncited], n_tasks=10)
    assert "UNDOCUMENTED for kimi-k2.6" in md_n and "heuristic caution" in md_n
    assert "--repeats>=3" in md_n


def test_dualrun_md_stochastic_bestshot_caveat():
    row = _drow("glm-5.2", paired=PairedResult(1, 0, 0, 1.0, False),
                stochastic_bestshot=True, tuned_temperature=0.7)
    md = report.render_dualrun_md([row], n_tasks=10)
    assert "stochastic_bestshot: best-shot enables sampling temperature=0.7" in md
    assert "--repeats>=3" in md


def test_dualrun_md_causal_and_composition_flags():
    row = _drow("m", paired=PairedResult(1, 0, 0, 1.0, False), gateway_capped=True,
                tuning_induced_regression=False, order_confound_suspect=True,
                task_composition_mismatch=True, n_tasks_mismatch=True)
    md = report.render_dualrun_md([row], n_tasks=10)
    assert "gateway_capped: reliability gap persists on the SAME gateway" in md
    assert "order_confound_suspect" in md and "cannot fully separate" in md   # residual-limitation line
    assert "task_composition_mismatch" in md and "n_tasks_mismatch" in md


def test_dualrun_md_no_data_and_empty_intersection():
    nd = _drow("dead", no_data=True, no_data_reason="a phase produced zero completed tasks")
    md = report.render_dualrun_md([nd], n_tasks=10)
    assert "NO DATA" in md and "no paired delta computed" in md
    ei = _drow("split", paired=PairedResult(0, 0, 0, 1.0, False), empty_intersection=True)
    md2 = report.render_dualrun_md([ei], n_tasks=10)
    assert "empty_intersection" in md2


def test_dualrun_md_low_confidence_banner_over_half():
    rows = [_drow("a", paired=PairedResult(1, 0, 0, 1.0, False), low_confidence=True,
                  low_confidence_reasons=["small_margin"]),
            _drow("b", paired=PairedResult(1, 0, 0, 1.0, False), low_confidence=True,
                  low_confidence_reasons=["all_operational"]),
            _drow("c", paired=PairedResult(1, 0, 0, 1.0, False), low_confidence=False)]
    md = report.render_dualrun_md(rows, n_tasks=10, n_tuned_records=3)
    assert "tuned settings are LOW-CONFIDENCE for 2 of 3 models" in md
    assert "treat this delta as indicative" in md              # per-row annotation too


def test_dualrun_md_corpus_power_and_multiplicity_caveat():
    md = report.render_dualrun_md([_drow("a", paired=PairedResult(1, 0, 0, 1.0, False))],
                                  n_tasks=10, n_tuned_records=1)
    assert "N_tuned_models=1" in md
    assert "do not vote-count" in md and "cross-model" in md


def test_dualrun_md_leakage_and_drift_blocks():
    prov = {"scored_dir": "/x/tasks", "dev_dir": None, "leakage_checked": True,
            "dev_corpus": {"tree_sha": "abc123", "oracle_ref_sha256": "def456",
                           "dev_tasks": ["quick-a", "quick-b"]}}
    md = report.render_dualrun_md([_drow("m", paired=PairedResult(1, 0, 0, 1.0, False))],
                                  n_tasks=10, provenance=prov,
                                  drift_notes=["WARNING: tuned record base for m drifted"])
    assert "## Leakage and provenance" in md
    assert "leakage check: disjoint (verified)" in md
    assert "tree_sha=abc123" in md
    assert "## Tuned-spec reconstruction notes" in md and "drifted" in md


def test_dualrun_summary_json_records_deltas_pass_rates_and_provenance():
    row = _drow("m", paired=PairedResult(n_paired=3, b=1, c=2, p_value=0.5, significant=False),
                pass_fraction_delta=0.33, ci_overlap=True, order_confound_suspect=True,
                tuned_temperature=0.7)
    pm = {"m": {"baseline": {"t1": TaskMetric(True, 1.0, 0.0, None, 1.0),
                             "t2": TaskMetric(False, 1.0, 0.0, None, 1 / 3)},
                "bestshot": {"t1": TaskMetric(True, 1.0, 0.0, None, 1.0),
                             "t2": TaskMetric(True, 1.0, 0.0, None, 1.0)}}}
    data = json.loads(report.render_dualrun_summary_json(
        [row], phase_metrics=pm, run_id="r", n_tasks=10, order="alternate",
        provenance={"scored_dir": "/x"}, orders={"m": "baseline-first"}, n_tuned_records=1))
    m = data["models"][0]
    assert m["paired"] == {"n_paired": 3, "b": 1, "c": 2, "p_value": 0.5, "significant": False}
    assert m["ci_overlap"] is True and m["order_confound_suspect"] is True
    assert m["baseline_pass_rates"] == {"t1": 1.0, "t2": 1 / 3}   # D6g flakiness recorded
    assert m["bestshot_pass_rates"] == {"t1": 1.0, "t2": 1.0}
    assert m["order"] == "baseline-first"
    assert data["order"] == "alternate" and data["provenance"] == {"scored_dir": "/x"}
    assert data["n_tuned_records"] == 1
