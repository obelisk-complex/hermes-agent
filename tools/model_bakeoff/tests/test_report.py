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
