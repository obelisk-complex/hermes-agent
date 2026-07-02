"""rank.assemble() + wilson_ci() (SPEC §2, §9). Offline, pure."""
from __future__ import annotations

from tools.model_bakeoff import rank
from tools.model_bakeoff.models import ModelAggregate


def agg(key, passed, n, reasoning=True, cost=0.0, p50=1.0):
    return ModelAggregate(
        model_key=key, reasoning=reasoning, cost_model="subscription",
        n_tasks=n, n_passed=passed, cost_per_task_usd=cost, p50_latency_s=p50,
    )


# --- Sub-project B Task 2: per-gateway reliability rollup + reliability notes ---

def _relagg(key, n, npass, gw, nop):
    return ModelAggregate(model_key=key, reasoning=False, cost_model="subscription",
                          n_tasks=n, n_passed=npass, gateway=gw, n_operational=nop)


def test_gateway_reliability_rolls_up_by_gateway():
    aggs = [_relagg("a", 10, 8, "opencode-go", 2), _relagg("b", 10, 9, "opencode-go", 0),
            _relagg("c", 10, 10, "ollama-cloud", 0)]
    gr = rank.gateway_reliability(aggs)
    assert gr["opencode-go"] == {"attempts": 20, "operational": 2, "failure_rate": 0.1}
    assert gr["ollama-cloud"] == {"attempts": 10, "operational": 0, "failure_rate": 0.0}


def test_assemble_attaches_gr_and_divergence_note():
    res = rank.assemble([_relagg("alpha", 10, 8, "opencode-go", 2)])
    assert res.gateway_reliability["opencode-go"]["operational"] == 2
    assert any("alpha" in n and "operational" in n.lower() for n in res.notes)


def test_assemble_survives_all_operational_model():
    res = rank.assemble([_relagg("dead", 6, 0, "opencode-go", 6)])   # completed_pass_fraction is None
    note = next(n for n in res.notes if "dead" in n and "operational" in n.lower())
    assert "n/a" in note                                             # None rendered as n/a, no crash
    assert "—" not in note and "–" not in note                      # house style


def test_excluded_note_flags_operational():
    res = rank.assemble([_relagg("op", 10, 0, "opencode-go", 10),    # excluded, all operational
                         _relagg("good", 10, 10, "ollama-cloud", 0)], bar=0.8)
    excl = next(n for n in res.notes if "excluded" in n.lower())
    assert "op" in excl and "operational" in excl.lower()


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


# --- Task 6: elegance breaks pass-fraction ties in the REPORT order, never in the ladder ---

def test_report_order_breaks_pass_ties_by_elegance_but_ladder_ignores_it():
    # Names chosen so an alphabetical tiebreak would put a_low first; elegance must override
    # it in the report, while the ladder (pass_fraction, cost, name) stays name-ordered.
    a_low = ModelAggregate(model_key="a_low", reasoning=True, cost_model="subscription",
                           n_tasks=10, n_passed=8, cost_per_task_usd=0.0, p50_latency_s=1.0,
                           mean_elegance=0.5)
    z_high = ModelAggregate(model_key="z_high", reasoning=True, cost_model="subscription",
                            n_tasks=10, n_passed=8, cost_per_task_usd=0.0, p50_latency_s=1.0,
                            mean_elegance=0.9)
    result = rank.assemble([a_low, z_high], ceiling_key=None, bar=0.0)
    assert [r.model_key for r in result.report_rows] == ["z_high", "a_low"]  # elegance wins the tie
    assert result.ladder == ["a_low", "z_high"]   # ladder unchanged: elegance did NOT leak in


def test_uniform_none_elegance_leaves_report_order_unchanged():
    # Regression: when nobody is judged (all mean_elegance None), the elegance term is a constant
    # and ordering falls through to cost/p50/name exactly as before Task 6.
    x = ModelAggregate(model_key="x", reasoning=True, cost_model="subscription",
                       n_tasks=10, n_passed=7, cost_per_task_usd=0.0, p50_latency_s=2.0)
    y = ModelAggregate(model_key="y", reasoning=True, cost_model="subscription",
                       n_tasks=10, n_passed=7, cost_per_task_usd=0.0, p50_latency_s=1.0)
    result = rank.assemble([x, y], ceiling_key=None, bar=0.0)
    assert [r.model_key for r in result.report_rows] == ["y", "x"]  # faster p50 first, as before
