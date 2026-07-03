"""rank.assemble() + wilson_ci() (SPEC §2, §9). Offline, pure."""
from __future__ import annotations

from tools.model_bakeoff import rank
from tools.model_bakeoff.models import ModelAggregate, ModelSpec, TaskMetric


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


# --- Sub-project D Task 3: paired McNemar exact significance ---

def test_paired_significance_no_discordant_is_p1_not_significant():
    r = rank.paired_significance({"t1": True, "t2": False}, {"t1": True, "t2": False})
    assert r.b == 0 and r.c == 0 and r.n_paired == 2
    assert r.p_value == 1.0 and r.significant is False


def test_paired_significance_all_improvements_is_significant():
    base = {f"t{i}": False for i in range(8)}
    best = {f"t{i}": True for i in range(8)}
    r = rank.paired_significance(base, best)
    assert r.b == 0 and r.c == 8
    assert r.p_value < 0.05 and r.significant is True


def test_paired_significance_balanced_discordant_is_not_significant():
    base = {f"t{i}": (i < 4) for i in range(8)}    # first 4 pass baseline, fail best-shot -> b
    best = {f"t{i}": (i >= 4) for i in range(8)}   # last 4 fail baseline, pass best-shot -> c
    r = rank.paired_significance(base, best)
    assert r.b == 4 and r.c == 4
    assert r.p_value == 1.0 and r.significant is False


def test_paired_significance_only_shared_task_ids_are_paired():
    base = {"shared1": True, "shared2": False, "only_base": True}
    best = {"shared1": False, "shared2": True, "only_best": False}
    r = rank.paired_significance(base, best)
    assert r.n_paired == 2                          # one-sided keys excluded
    assert r.b == 1 and r.c == 1                     # shared1 regressed, shared2 improved


def test_paired_significance_exact_small_binomial():
    # b=1, c=5, n=6, k=1: 2 * (C(6,0)+C(6,1)) * 0.5^6 = 2*7/64 = 0.21875
    base = {"a": True, **{f"b{i}": False for i in range(5)}}
    best = {"a": False, **{f"b{i}": True for i in range(5)}}
    r = rank.paired_significance(base, best)
    assert r.b == 1 and r.c == 5
    assert abs(r.p_value - 0.21875) < 1e-9


# --- Sub-project D Task 5: tuning_delta (fully-paired axes + flags + empty-safe) ---

def _tm(passed, latency=1.0, cost=0.0, elegance=None, pass_rate=None):
    return TaskMetric(passed=passed, latency_s=latency, cost_proxy_usd=cost, elegance=elegance,
                      pass_rate=(pass_rate if pass_rate is not None else (1.0 if passed else 0.0)))


def _dagg(key="m", n=10, npass=8, gw="opencode-go", nop=0, p50=1.0, cost_proxy=0.001,
          elegance=None, n_eleg=0, cost_per_task=0.0):
    return ModelAggregate(model_key=key, reasoning=False, cost_model="subscription",
                          n_tasks=n, n_passed=npass, gateway=gw, n_operational=nop,
                          p50_latency_s=p50, cost_proxy_per_task_usd=cost_proxy,
                          mean_elegance=elegance, n_elegance_judged=n_eleg,
                          cost_per_task_usd=cost_per_task)


def _dspec(key="m", omit_temp=False, reasoning=False, temperature=None, gateway="opencode-go"):
    return ModelSpec(key=key, gateway=gateway, wire_id=key, cost_model="subscription",
                     reasoning=reasoning, omit_temp=omit_temp, max_tokens=8000, api_timeout_s=180,
                     temperature=temperature)


def test_tuning_delta_paired_accuracy_over_intersection():
    bm = {"t1": _tm(True), "t2": _tm(False), "t3": _tm(False)}
    sm = {"t1": _tm(True), "t2": _tm(True), "t3": _tm(True)}     # t2,t3 improved, t1 unchanged
    row = rank.tuning_delta(_dagg(n=3, npass=1), _dagg(n=3, npass=3), bm, sm,
                            _dspec(), _dspec(), repeats=1)
    assert row.paired.n_paired == 3 and row.paired.c == 2 and row.paired.b == 0
    assert abs(row.baseline_pass_fraction - 1 / 3) < 1e-9
    assert abs(row.bestshot_pass_fraction - 1.0) < 1e-9
    assert abs(row.pass_fraction_delta - 2 / 3) < 1e-9


def test_tuning_delta_speed_is_median_of_per_task_differences():
    # per-task diffs [1,8,1] -> median 1; difference-of-medians would be 10-2=8 (a non-paired artefact)
    bm = {"t1": _tm(True, latency=1.0), "t2": _tm(True, latency=2.0), "t3": _tm(True, latency=9.0)}
    sm = {"t1": _tm(True, latency=2.0), "t2": _tm(True, latency=10.0), "t3": _tm(True, latency=10.0)}
    row = rank.tuning_delta(_dagg(n=3, npass=3), _dagg(n=3, npass=3), bm, sm,
                            _dspec(), _dspec(), repeats=1)
    assert row.p50_paired_delta == 1.0
    assert row.p50_paired_delta != 8.0             # NOT difference-of-medians


def test_tuning_delta_paired_cost_and_elegance():
    bm = {"t1": _tm(True, cost=0.001, elegance=0.6), "t2": _tm(True, cost=0.002, elegance=0.5)}
    sm = {"t1": _tm(True, cost=0.003, elegance=0.8), "t2": _tm(True, cost=0.004, elegance=0.9)}
    row = rank.tuning_delta(_dagg(n=2, npass=2), _dagg(n=2, npass=2), bm, sm,
                            _dspec(), _dspec(), repeats=1)
    assert abs(row.cost_proxy_paired_delta - 0.002) < 1e-9   # mean of [0.002, 0.002]
    assert abs(row.elegance_paired_delta - 0.3) < 1e-9        # mean of [0.2, 0.4]


def test_tuning_delta_none_safety_unjudged_and_none_latency():
    bm = {"t1": _tm(True, latency=None, elegance=None)}
    sm = {"t1": _tm(True, latency=None, elegance=None)}
    row = rank.tuning_delta(_dagg(n=1, npass=1, p50=None), _dagg(n=1, npass=1, p50=None),
                            bm, sm, _dspec(), _dspec(), repeats=1)
    assert row.p50_paired_delta is None            # no clean latency in either phase
    assert row.elegance_paired_delta is None        # nothing judged
    assert row.cost_proxy_paired_delta is not None  # cost proxy is always present


def test_tuning_delta_empty_intersection_no_crash():
    bm = {"t1": _tm(True, latency=1.0, elegance=0.5)}
    sm = {"t2": _tm(True, latency=2.0, elegance=0.9)}   # disjoint completed sets, both n_tasks>0
    row = rank.tuning_delta(_dagg(n=1, npass=1), _dagg(n=1, npass=1), bm, sm,
                            _dspec(), _dspec(), repeats=1)
    assert row.empty_intersection is True and row.no_data is False
    assert row.pass_fraction_delta is None
    assert row.p50_paired_delta is None
    assert row.elegance_paired_delta is None
    assert row.cost_proxy_paired_delta is None
    assert row.task_composition_mismatch is True


def test_tuning_delta_no_data_when_phase_zero_tasks():
    row = rank.tuning_delta(_dagg(n=0, npass=0), _dagg(n=5, npass=3), {}, {"t1": _tm(True)},
                            _dspec(), _dspec(), repeats=1)
    assert row.no_data is True
    assert row.pass_fraction_delta is None and row.paired is None


def test_tuning_delta_flags_count_and_composition_mismatch():
    bm = {"t1": _tm(True), "t2": _tm(True)}
    sm = {"t1": _tm(True)}
    row = rank.tuning_delta(_dagg(n=10, npass=8), _dagg(n=9, npass=7), bm, sm,
                            _dspec(), _dspec(), repeats=1)
    assert row.n_tasks_mismatch is True            # 10 != 9
    assert row.task_composition_mismatch is True    # {t1,t2} != {t1}


def test_tuning_delta_ci_overlap_flag():
    row = rank.tuning_delta(_dagg(n=10, npass=8), _dagg(n=10, npass=8),
                            {"t1": _tm(True)}, {"t1": _tm(True)}, _dspec(), _dspec(), 1)
    assert row.ci_overlap is True                  # identical CIs overlap
    row2 = rank.tuning_delta(_dagg(n=10, npass=0), _dagg(n=10, npass=10),
                             {"t1": _tm(False)}, {"t1": _tm(True)}, _dspec(), _dspec(), 1)
    assert row2.ci_overlap is False                # [0,.28] vs [.72,1] do not overlap


def test_tuning_delta_gateway_capped_and_regression():
    capped = rank.tuning_delta(_dagg(nop=3), _dagg(nop=3), {"t": _tm(True)}, {"t": _tm(True)},
                               _dspec(), _dspec(), 1)
    assert capped.gateway_capped is True and capped.tuning_induced_regression is False
    reg = rank.tuning_delta(_dagg(nop=0), _dagg(nop=2), {"t": _tm(True)}, {"t": _tm(True)},
                            _dspec(), _dspec(), 1)
    assert reg.tuning_induced_regression is True and reg.gateway_capped is False
    clean = rank.tuning_delta(_dagg(nop=0), _dagg(nop=0), {"t": _tm(True)}, {"t": _tm(True)},
                              _dspec(), _dspec(), 1)
    assert clean.gateway_capped is False and clean.tuning_induced_regression is False


def test_tuning_delta_gateway_capped_requires_same_gateway():
    # different gateways -> neither capped nor tuning-induced-regression, but gateway_changed noted
    diff = rank.tuning_delta(_dagg(nop=3, gw="opencode-go"), _dagg(nop=4, gw="ollama-cloud"),
                             {"t": _tm(True)}, {"t": _tm(True)},
                             _dspec(gateway="opencode-go"), _dspec(gateway="ollama-cloud"), 1)
    assert diff.gateway_capped is False and diff.gateway_changed is True


def test_tuning_delta_order_confound_suspect():
    slow = rank.tuning_delta(_dagg(p50=1.0), _dagg(p50=3.0), {"t": _tm(True)}, {"t": _tm(True)},
                             _dspec(), _dspec(), 1)
    assert slow.order_confound_suspect is True     # p50 ratio > 2
    safe = rank.tuning_delta(_dagg(p50=None, nop=0), _dagg(p50=None, nop=0),
                             {"t": _tm(True)}, {"t": _tm(True)}, _dspec(), _dspec(), 1)
    assert safe.order_confound_suspect is False     # None p50, no op delta, no crash
    opdelta = rank.tuning_delta(_dagg(p50=1.0, nop=0), _dagg(p50=1.0, nop=2),
                                {"t": _tm(True)}, {"t": _tm(True)}, _dspec(), _dspec(), 1)
    assert opdelta.order_confound_suspect is True   # operational delta alone trips it


def test_tuning_delta_stochastic_bestshot_flag():
    hot = rank.tuning_delta(_dagg(), _dagg(), {"t": _tm(True)}, {"t": _tm(True)},
                            _dspec(temperature=0.0), _dspec(temperature=0.7), repeats=1)
    assert hot.stochastic_bestshot is True and hot.tuned_temperature == 0.7
    warmreps = rank.tuning_delta(_dagg(), _dagg(), {"t": _tm(True)}, {"t": _tm(True)},
                                 _dspec(temperature=0.0), _dspec(temperature=0.7), repeats=3)
    assert warmreps.stochastic_bestshot is False    # repeats>1 controls the noise
    cold = rank.tuning_delta(_dagg(), _dagg(), {"t": _tm(True)}, {"t": _tm(True)},
                             _dspec(), _dspec(temperature=None), repeats=1)
    assert cold.stochastic_bestshot is False


def test_tuning_delta_sampling_uncontrolled_fires_for_reasoner_without_temperature():
    # an omit_temp=True reasoner cannot carry a temperature, so stochastic_bestshot stays False,
    # yet sampling_uncontrolled must fire (vendor-internal thinking-mode non-determinism).
    row = rank.tuning_delta(_dagg(), _dagg(), {"t": _tm(True)}, {"t": _tm(True)},
                            _dspec(omit_temp=True, reasoning=True),
                            _dspec(omit_temp=True, reasoning=True), repeats=1)
    assert row.sampling_uncontrolled is True
    assert row.stochastic_bestshot is False
    plain = rank.tuning_delta(_dagg(), _dagg(), {"t": _tm(True)}, {"t": _tm(True)},
                              _dspec(omit_temp=False, reasoning=False),
                              _dspec(omit_temp=False, reasoning=False), repeats=1)
    assert plain.sampling_uncontrolled is False
