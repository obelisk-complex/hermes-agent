"""report rendering (SPEC §9). Offline, pure."""
from __future__ import annotations

import json

from tools.model_bakeoff import rank, report
from tools.model_bakeoff.models import ModelAggregate


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
