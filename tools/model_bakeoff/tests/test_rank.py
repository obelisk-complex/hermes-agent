"""rank.assemble() + wilson_ci() (SPEC §2, §9). Offline, pure."""
from __future__ import annotations

from tools.model_bakeoff import rank
from tools.model_bakeoff.models import ModelAggregate


def agg(key, passed, n, reasoning=True, cost=0.0, p50=1.0):
    return ModelAggregate(
        model_key=key, reasoning=reasoning, cost_model="subscription",
        n_tasks=n, n_passed=passed, cost_per_task_usd=cost, p50_latency_s=p50,
    )


def test_wilson_bounds_perfect_and_zero():
    lo, hi = rank.wilson_ci(10, 10)
    assert 0.0 < lo < 1.0 and lo < hi <= 1.0
    lo0, hi0 = rank.wilson_ci(0, 10)
    assert lo0 == 0.0 and 0.0 < hi0 < 1.0
    assert rank.wilson_ci(0, 0) == (0.0, 0.0)


def test_ladder_weakest_first_with_ceiling_pinned_last():
    aggs = [agg("strong", 9, 10), agg("weak", 3, 10), agg("ceil", 10, 10, reasoning=False)]
    res = rank.assemble(aggs, ceiling_key="ceil")
    assert res.ladder[-1] == "ceil"
    assert [k for k in res.ladder if k != "ceil"] == ["weak", "strong"]


def test_report_keeps_reasoning_groups_separate():
    aggs = [agg("r1", 8, 10, reasoning=True), agg("n1", 9, 10, reasoning=False)]
    res = rank.assemble(aggs)
    assert res.report_rows[0].reasoning is True
    assert res.report_rows[-1].reasoning is False


def test_report_strongest_first_within_group():
    aggs = [agg("a", 5, 10), agg("b", 9, 10), agg("c", 7, 10)]
    res = rank.assemble(aggs)
    assert [a.model_key for a in res.report_rows] == ["b", "c", "a"]


def test_tie_break_by_cost_then_name():
    aggs = [agg("b", 8, 10, cost=0.5), agg("a", 8, 10, cost=0.2)]
    res = rank.assemble(aggs)
    assert [a.model_key for a in res.report_rows] == ["a", "b"]


def test_overlapping_cis_flag_indistinguishable_pair():
    aggs = [agg("x", 5, 6), agg("y", 4, 6)]
    res = rank.assemble(aggs)
    flat = set(res.indistinguishable_pairs) | {(b, a) for a, b in res.indistinguishable_pairs}
    assert ("x", "y") in flat


def test_bar_excludes_failing_models_from_ladder():
    aggs = [agg("good", 9, 10), agg("bad", 1, 10), agg("ceil", 10, 10, reasoning=False)]
    res = rank.assemble(aggs, ceiling_key="ceil", bar=0.5)
    assert "bad" not in res.ladder
    assert res.ladder == ["good", "ceil"]


def test_detect_contamination_flags_by_per_task_threshold():
    # n_attempted=4 => threshold = max(2, floor(3.0)) = 3
    assert rank.detect_contamination({"leaky": 3, "ok": 2}, {"leaky": 4, "ok": 4}) == ["leaky"]
    # n_attempted=2 => threshold = 2
    assert rank.detect_contamination({"t": 2}, {"t": 2}) == ["t"]
    assert rank.detect_contamination({"t": 1}, {"t": 2}) == []
    # fewer than 2 attempters can never be judged
    assert rank.detect_contamination({"t": 1}, {"t": 1}) == []
    assert rank.detect_contamination({}, {}) == []


def test_detect_contamination_returns_sorted():
    assert rank.detect_contamination({"b": 4, "a": 4}, {"b": 4, "a": 4}) == ["a", "b"]


def test_assemble_notes_bar_exclusion_and_degenerate_ladder():
    aggs = [agg("bad1", 1, 10), agg("bad2", 2, 10), agg("ceil", 10, 10, reasoning=False)]
    res = rank.assemble(aggs, ceiling_key="ceil", bar=0.8)
    assert res.ladder == ["ceil"]                       # every non-ceiling model excluded
    joined = " ".join(res.notes)
    assert "excluded" in joined and "bad1" in joined and "bad2" in joined
    assert any("<= 1 entry" in n for n in res.notes)    # fail-loud degenerate-ladder warning
