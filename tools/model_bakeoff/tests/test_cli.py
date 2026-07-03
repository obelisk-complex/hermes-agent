"""cli commands (SPEC §10). Offline: validate-oracles + estimate run for real;
run is driven end-to-end through a fake transport and writes all artefacts."""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

from tools.model_bakeoff import cli, corpus, registry


def test_validate_oracles_command_passes():
    assert cli.cmd_validate_oracles(SimpleNamespace(timeout=30)) == 0


def test_estimate_subscription_is_free_metered_is_not():
    tasks = corpus.load()
    sub = [m for m in registry.ROSTER if m.cost_model == "subscription"][:1]
    opus = [m for m in registry.ROSTER if m.key == "claude-opus-4-8"]
    _, sub_total, _ = cli.estimate(sub, tasks, repeats=3)
    _, opus_total, _ = cli.estimate(opus, tasks, repeats=3)
    assert sub_total == 0.0
    assert opus_total > 0.0


def test_estimate_flags_unpriced_metered_models():
    # metered but priceless => must be flagged, never silently counted as $0
    tasks = corpus.load()
    metered_unpriced = [m for m in registry.ROSTER
                        if m.is_metered and not (m.price_in_per_m and m.price_out_per_m)]
    assert metered_unpriced, "expected at least one unpriced metered model in the roster"
    _, _, unpriced = cli.estimate(metered_unpriced, tasks, repeats=1)
    assert set(unpriced) == {m.key for m in metered_unpriced}


def test_estimate_command_returns_2_when_over_budget():
    args = SimpleNamespace(models="claude-opus-4-8", repeats=3, budget=0.0)
    assert cli.cmd_estimate(args) == 2


def test_run_parser_accepts_suite():
    args = cli.build_parser().parse_args(["run", "--suite", "tag:ai-trap"])
    assert args.suite == "tag:ai-trap"


# --- Sub-project D Task 4: _phase_metrics reconstructs contract-pinned per-task metrics ---

def _priced_spec(price_out=0.5):
    from tools.model_bakeoff.models import ModelSpec
    return ModelSpec(key="pm", gateway="opencode-go", wire_id="pm", cost_model="subscription",
                     reasoning=False, omit_temp=False, max_tokens=8000, api_timeout_s=180,
                     price_out_per_m=price_out)


def _write_raw(raw_dir, model, task, rep, passed, error_type=None, latency_s=1.0,
               cache_hit=False, completion_tokens=100, thinking_tokens=0, elegance=None):
    """Write one raw file with the exact cli._persist_raw schema."""
    os.makedirs(raw_dir, exist_ok=True)
    rec = {"model": model, "task": task, "repeat_idx": rep, "passed": passed,
           "error_type": error_type, "latency_s": latency_s, "cache_hit": cache_hit,
           "cost_usd": 0.0, "prompt_tokens": 10, "completion_tokens": completion_tokens,
           "thinking_tokens": thinking_tokens, "extracted_code": "", "raw_response": "",
           "elegance": elegance, "elegance_rationale": "", "judge_cost_usd": 0.0}
    with open(os.path.join(raw_dir, f"{model}__{task}__r{rep}.json"), "w") as f:
        json.dump(rec, f)


def test_phase_metrics_includes_exact_repeats_all_pass(tmp_path):
    from tools.model_bakeoff import client
    raw = str(tmp_path / "raw")
    spec = _priced_spec(price_out=0.5)
    _write_raw(raw, spec.key, "quick-a", 0, passed=True, latency_s=2.0, completion_tokens=100)
    m = cli._phase_metrics(raw, repeats=1, spec=spec)
    assert set(m) == {"quick-a"}
    tm = m["quick-a"]
    assert tm.passed is True and tm.pass_rate == 1.0
    assert tm.latency_s == 2.0
    assert tm.cost_proxy_usd == client.cost_proxy_usd(spec, 100, 0)   # priced via the passed spec


def test_phase_metrics_omits_operational_task(tmp_path):
    raw = str(tmp_path / "raw")
    spec = _priced_spec()
    _write_raw(raw, spec.key, "quick-op", 0, passed=False, error_type="call_error")
    m = cli._phase_metrics(raw, repeats=1, spec=spec)
    assert "quick-op" not in m                    # operational -> OMITTED entirely, never a False


def test_phase_metrics_omits_partial_repeat_count(tmp_path):
    raw = str(tmp_path / "raw")
    spec = _priced_spec()
    _write_raw(raw, spec.key, "quick-part", 0, passed=True)   # only 1 of the 3 expected files
    m = cli._phase_metrics(raw, repeats=3, spec=spec)
    assert "quick-part" not in m


def test_phase_metrics_non_operational_failure_included_as_false(tmp_path):
    raw = str(tmp_path / "raw")
    spec = _priced_spec()
    _write_raw(raw, spec.key, "quick-flaky", 0, passed=True, latency_s=1.0)
    _write_raw(raw, spec.key, "quick-flaky", 1, passed=True, latency_s=3.0)
    _write_raw(raw, spec.key, "quick-flaky", 2, passed=False, error_type="test_failure", latency_s=2.0)
    m = cli._phase_metrics(raw, repeats=3, spec=spec)
    assert "quick-flaky" in m                      # non-operational failure -> included
    tm = m["quick-flaky"]
    assert tm.passed is False and abs(tm.pass_rate - 2 / 3) < 1e-9
    assert tm.latency_s == 2.0                     # median of [1,3,2]


def test_phase_metrics_median_latency_and_elegance_mean(tmp_path):
    raw = str(tmp_path / "raw")
    spec = _priced_spec()
    _write_raw(raw, spec.key, "quick-e", 0, passed=True, latency_s=1.0, elegance=0.8)
    _write_raw(raw, spec.key, "quick-e", 1, passed=True, latency_s=5.0, elegance=0.6)
    tm = cli._phase_metrics(raw, repeats=2, spec=spec)["quick-e"]
    assert tm.latency_s == 3.0                     # median of [1,5]
    assert abs(tm.elegance - 0.7) < 1e-9           # mean of [0.8,0.6]


def test_phase_metrics_all_cache_hit_latency_none(tmp_path):
    raw = str(tmp_path / "raw")
    spec = _priced_spec()
    _write_raw(raw, spec.key, "quick-c", 0, passed=True, latency_s=0.05, cache_hit=True, elegance=None)
    tm = cli._phase_metrics(raw, repeats=1, spec=spec)["quick-c"]
    assert tm.latency_s is None                    # every sample cache-hit-excluded
    assert tm.elegance is None                     # none judged


def test_estimate_and_validate_oracles_parsers_accept_suite():
    assert cli.build_parser().parse_args(["estimate", "--suite", "quick"]).suite == "quick"
    assert cli.build_parser().parse_args(["validate-oracles", "--suite", "quick"]).suite == "quick"


def test_validate_suites_subcommand_exists():
    args = cli.build_parser().parse_args(["validate-suites"])
    assert hasattr(args, "disjoint")
    assert args.func is cli.cmd_validate_suites


def test_run_writes_all_artefacts(tmp_path):
    async def fake(url, headers, json_body, timeout):
        return 200, {"choices": [{"message": {"content": "```python\ndef f(x):\n    return x\n```"}}],
                     "usage": {"prompt_tokens": 10, "completion_tokens": 5}}, None

    # transport kwargs are named url/headers/json/timeout in client; bind by name
    async def fake_transport(url, headers, json, timeout):
        return await fake(url, headers, json, timeout)

    env = {"BAKEOFF_GATEWAY_URL": "https://x/v1", "BAKEOFF_GATEWAY_KEY": "k"}
    out = str(tmp_path / "run1")
    args = SimpleNamespace(models="deepseek-v4-flash,claude-opus-4-8",
                           repeats=1, budget=10.0, out=out, bar=0.8, sandbox_timeout=60)
    rc = cli.cmd_run(args, env=env, transport=fake_transport)
    assert rc == 0
    assert os.path.exists(os.path.join(out, "report.md"))
    assert os.path.exists(os.path.join(out, "ladder.yaml"))
    summary = json.load(open(os.path.join(out, "summary.json")))
    assert summary["ladder"][-1] == "claude-opus-4-8"  # ceiling pinned last
    assert os.listdir(os.path.join(out, "raw"))  # raw model outputs persisted
    assert summary["suite"] == {"selector": None, "task_ids": [t.task_id for t in corpus.load()]}


def test_run_with_suite_narrows_execution_and_records_it(tmp_path):
    # end-to-end proof that --suite narrows what actually RUNS (not just the metadata)
    # and that the resolved task_ids are recorded. Guards the cmd_run->run_bakeoff->report seam.
    async def fake_transport(url, headers, json, timeout):
        return 200, {"choices": [{"message": {"content": "```python\ndef f(x):\n    return x\n```"}}],
                     "usage": {"prompt_tokens": 10, "completion_tokens": 5}}, None

    env = {"BAKEOFF_GATEWAY_URL": "https://x/v1", "BAKEOFF_GATEWAY_KEY": "k"}
    out = str(tmp_path / "run_ai_traps")
    args = SimpleNamespace(models="deepseek-v4-flash,claude-opus-4-8",
                           repeats=1, budget=10.0, out=out, bar=0.8, sandbox_timeout=60,
                           suite="tag:ai-trap")
    rc = cli.cmd_run(args, env=env, transport=fake_transport)
    assert rc == 0
    summary = json.load(open(os.path.join(out, "summary.json")))
    ai_traps = ["quick-overlapping-substring-count", "standard-halfopen-merge-intervals",
                "thorough-expr-eval"]
    assert summary["n_tasks"] == 3                         # only the 3 tasks ran, not all 10
    assert summary["suite"] == {"selector": "tag:ai-trap", "task_ids": ai_traps}
    assert "Suite: tag:ai-trap" in open(os.path.join(out, "report.md")).read()


def test_run_and_estimate_default_repeats_is_one_and_run_has_bar_and_sandbox_timeout():
    p = cli.build_parser()
    assert p.parse_args(["run"]).repeats == 1
    assert p.parse_args(["estimate"]).repeats == 1
    assert p.parse_args(["run"]).bar == 0.8
    assert p.parse_args(["run"]).sandbox_timeout == 60


def test_run_bakeoff_flags_a_contaminated_task(tmp_path):
    import asyncio
    from tools.model_bakeoff.models import TaskSpec

    (tmp_path / "p.md").write_text("return 1 from f()")
    (tmp_path / "o_test.py").write_text("from solution import f\n\ndef test_f():\n    assert f() == 1\n")
    (tmp_path / "r.py").write_text("def f():\n    return 1\n")
    task = TaskSpec("leaky", "quick", str(tmp_path / "p.md"),
                    str(tmp_path / "o_test.py"), str(tmp_path / "r.py"))

    passers = {"deepseek-v4-flash", "glm-5.1", "glm-5.2"}  # 3 of 4 attempters pass perfectly

    async def transport(url, headers, json, timeout):
        code = "def f():\n    return 1" if json["model"] in passers else "def f():\n    return 0"
        return 200, {"choices": [{"message": {"content": f"```python\n{code}\n```"}}],
                     "usage": {"prompt_tokens": 1, "completion_tokens": 1}}, 0.2

    models = [registry.by_key(k) for k in ["deepseek-v4-flash", "glm-5.1", "glm-5.2", "kimi-k2.6"]]
    env = {"BAKEOFF_GATEWAY_URL": "https://x/v1", "BAKEOFF_GATEWAY_KEY": "k"}
    result, _ = asyncio.run(cli.run_bakeoff(models, [task], env, str(tmp_path / "run"),
                                            10.0, 1, transport, bar=0.0))
    assert "leaky" in result.contamination_flags  # 3 of 4 (>= floor(0.75*4)=3) => flagged


def _trivial_task(tmp_path):
    from tools.model_bakeoff.models import TaskSpec
    (tmp_path / "p.md").write_text("write f returning 1")
    (tmp_path / "o_test.py").write_text("from solution import f\n\ndef test_f():\n    assert f() == 1\n")
    (tmp_path / "r.py").write_text("def f():\n    return 1\n")
    return TaskSpec("t", "quick", str(tmp_path / "p.md"),
                    str(tmp_path / "o_test.py"), str(tmp_path / "r.py"))


def test_showed_reasoning_requires_closed_block_not_bare_tag():
    # M1: a bare <think> in prose/code must NOT count as reasoning; a closed block or tokens do.
    from tools.model_bakeoff.models import CallResult
    bare = CallResult(model_key="m", task_id="t", ok=True,
                      raw_response="# parses <think> elements", thinking_tokens=0)
    assert cli._showed_reasoning(bare) is False
    closed = CallResult(model_key="m", task_id="t", ok=True,
                        raw_response="<think>reasoning here</think>", thinking_tokens=0)
    assert cli._showed_reasoning(closed) is True
    via_tokens = CallResult(model_key="m", task_id="t", ok=True, raw_response="", thinking_tokens=5)
    assert cli._showed_reasoning(via_tokens) is True


def test_run_bakeoff_warns_when_reasoning_model_shows_only_bare_think_tag(tmp_path):
    # M1 smoke: a reasoning model whose response only mentions <think> (no closed block) must
    # trip the fail-loud zero-reasoning WARNING.
    import asyncio
    task = _trivial_task(tmp_path)

    async def transport(url, headers, json, timeout):
        return 200, {"choices": [{"message": {"content":
                     "# uses <think> here\n```python\ndef f():\n    return 1\n```"}}],
                     "usage": {}}, 0.2

    models = [registry.by_key("deepseek-v4-flash")]  # reasoning=True
    env = {"BAKEOFF_GATEWAY_URL": "https://x/v1", "BAKEOFF_GATEWAY_KEY": "k"}
    result, _ = asyncio.run(cli.run_bakeoff(models, [task], env, str(tmp_path / "run"),
                                            10.0, 1, transport, bar=0.0))
    assert any("WARNING" in n and "deepseek-v4-flash" in n for n in result.notes)


def test_run_bakeoff_budget_stop_persists_partial_runs(tmp_path):
    # M3/A-005: a budget-stop mid-model must still persist + count the completed task run.
    import asyncio
    task = _trivial_task(tmp_path)

    async def transport(url, headers, json, timeout):
        return 200, {"choices": [{"message": {"content": "```python\ndef f():\n    return 1\n```"}}],
                     "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}}, 0.2

    out = str(tmp_path / "run")
    models = [registry.by_key("claude-opus-4-8")]  # metered + priced -> $30 on task 1 trips the cap
    env = {"BAKEOFF_GATEWAY_URL": "https://x/v1", "BAKEOFF_GATEWAY_KEY": "k"}
    result, _ = asyncio.run(cli.run_bakeoff(models, [task], env, out, 0.0001, 1, transport))
    assert any("BUDGET STOP" in n for n in result.notes)          # passes pre+post (not the driver)
    assert os.listdir(os.path.join(out, "raw"))                   # discriminating: empty pre-fix
    opus_rows = [r for r in result.report_rows if r.model_key == "claude-opus-4-8"]
    assert len(opus_rows) == 1 and opus_rows[0].n_tasks >= 1      # discriminating: [] pre-fix


def test_run_bakeoff_default_bar_excludes_sub_bar_model(tmp_path):
    # L1: run_bakeoff's programmatic default bar must match the CLI (0.8), not the old 0.0.
    import asyncio
    task = _trivial_task(tmp_path)

    async def transport(url, headers, json, timeout):
        wrong = json["model"] != "claude-opus-4-8"
        body = "def f():\n    return 0" if wrong else "def f():\n    return 1"
        return 200, {"choices": [{"message": {"content": f"```python\n{body}\n```"}}],
                     "usage": {"prompt_tokens": 1, "completion_tokens": 1}}, 0.2

    models = [registry.by_key("glm-5.1"), registry.by_key("claude-opus-4-8")]
    env = {"BAKEOFF_GATEWAY_URL": "https://x/v1", "BAKEOFF_GATEWAY_KEY": "k"}
    # called WITHOUT bar= -> exercises the run_bakeoff signature default
    result, _ = asyncio.run(cli.run_bakeoff(models, [task], env, str(tmp_path / "run"), 10.0, 1, transport))
    assert "glm-5.1" not in result.ladder            # 0% pass excluded at default bar 0.8
    assert result.ladder[-1] == "claude-opus-4-8"    # ceiling still pinned last


# --- Task 8 (A21/A22): per-model + floored run-wide judge-outage escalation ---

def _esc(att, unp):
    notes = []
    cli._emit_judge_escalation(att, unp, notes)
    return notes


def test_escalation_per_model_concentrated_outage_names_the_model():
    # model-B all 3 dark, model-A all 3 fine: run-wide is only 50% (below floors) but B must warn.
    notes = _esc({"A": 3, "B": 3}, {"A": 0, "B": 3})
    assert any("UNAVAILABLE" in n and "B" in n for n in notes)
    assert not any("UNAVAILABLE" in n and n.count("A") and "for A" in n for n in notes)
    assert any(n.startswith("note:") and "3 of 6" in n for n in notes)   # run-wide quiet rollup
    assert not any("run were HTTP 200" in n for n in notes)              # no run-wide WARNING


def test_escalation_per_model_near_total_fraction_and_floor():
    assert any("near-total" in n and "for M" in n for n in _esc({"M": 5}, {"M": 4}))   # 0.8 fraction
    assert any("near-total" in n and "for M" in n for n in _esc({"M": 3}, {"M": 2}))   # j=1 < 2


def test_escalation_per_model_floor_suppresses_tiny():
    # a<3 -> no per-model WARNING (a run-wide quiet 'note:' may still appear; that is fine).
    assert not any(n.startswith("WARNING") for n in _esc({"M": 2}, {"M": 2}))


def test_escalation_run_wide_total_and_incidental():
    # 2 models all dark (att=3 each) -> per-model UNAVAILABLE x2 AND run-wide UNAVAILABLE
    notes = _esc({"A": 3, "B": 3}, {"A": 3, "B": 3})
    assert sum("UNAVAILABLE" in n and "for" not in n and "run" in n for n in notes) == 1
    assert sum(("elegance axis for" in n and "UNAVAILABLE" in n) for n in notes) == 2
    # incidental tiny run (att=1 x3) -> NO warning, only the quiet note. (The quiet note itself
    # says "WARNINGs above", so detect warnings by the 'WARNING:' prefix, not a loose substring.)
    tiny = _esc({"A": 1, "B": 1, "C": 1}, {"A": 1, "B": 1, "C": 1})
    assert not any(n.startswith("WARNING") for n in tiny)
    assert any(n.startswith("note:") and "3 of 3" in n for n in tiny)


def test_escalation_a22_call_errors_excluded_from_denominator():
    # A22: att counts only 200-responses; call-errors are excluded upstream. At a=3 all-dark the
    # wording must read "all 3 of its judged cells" (the true 200-response denominator), not "1 of 4".
    assert not any(n.startswith("WARNING") for n in _esc({"M": 2}, {"M": 2}))  # below floor
    assert any("all 3 of its judged cells" in n for n in _esc({"M": 3}, {"M": 3}))


# --- Task 8: run_bakeoff judge wiring (integration, offline) ---

def _combo_transport(judge_content='{"elegance": 0.75, "rationale": "clean"}',
                     solution="def f():\n    return 1", judge_status=200):
    """One transport for both roles: a judge call (prompt carries 'UNTRUSTED') returns judge JSON;
    any other call returns a candidate solution."""
    async def t(url, headers, json, timeout):
        prompt = json["messages"][0]["content"]
        if "UNTRUSTED" in prompt:
            if judge_status != 200:
                return judge_status, {"error": "x"}, 0.2
            return 200, {"choices": [{"message": {"content": judge_content}}],
                         "usage": {"prompt_tokens": 100, "completion_tokens": 20}}, 0.2
        return 200, {"choices": [{"message": {"content": f"```python\n{solution}\n```"}}],
                     "usage": {"prompt_tokens": 10, "completion_tokens": 5}}, 0.2
    return t


_FULL_ENV = {"BAKEOFF_GATEWAY_URL": "https://x/v1", "BAKEOFF_GATEWAY_KEY": "k"}


def test_run_bakeoff_rejects_self_grading_judge(tmp_path):
    import asyncio
    import dataclasses
    task = _trivial_task(tmp_path)
    jspec = dataclasses.replace(registry.judge_spec(), key="deepseek-judge", wire_id="deepseek-judge")
    try:
        asyncio.run(cli.run_bakeoff([registry.by_key("deepseek-v4-flash")], [task], _FULL_ENV,
                                    str(tmp_path / "r"), 10.0, 1, _combo_transport(),
                                    bar=0.0, judge_spec=jspec))
        assert False, "expected ValueError for a same-family (self-grading) judge"
    except ValueError:
        pass


def test_run_bakeoff_attaches_elegance_and_patches_raw_file(tmp_path):
    import asyncio
    import json as _json
    task = _trivial_task(tmp_path)
    out = str(tmp_path / "r")
    result, _ = asyncio.run(cli.run_bakeoff(
        [registry.by_key("deepseek-v4-flash")], [task], _FULL_ENV, out, 10.0, 1,
        _combo_transport(), bar=0.0))
    rows = {r.model_key: r for r in result.report_rows}
    assert rows["deepseek-v4-flash"].mean_elegance == 0.75
    assert rows["deepseek-v4-flash"].n_elegance_judged == 1
    data = _json.load(open(os.path.join(out, "raw", "deepseek-v4-flash__t__r0.json")))
    assert data["elegance"] == 0.75 and data["repeat_idx"] == 0


def test_run_bakeoff_judge_call_error_is_loudly_noted(tmp_path):
    import asyncio
    task = _trivial_task(tmp_path)
    result, _ = asyncio.run(cli.run_bakeoff(
        [registry.by_key("deepseek-v4-flash")], [task], _FULL_ENV, str(tmp_path / "r"), 10.0, 1,
        _combo_transport(judge_status=500), bar=0.0))
    assert any("gateway/wire_id" in n for n in result.notes)


def test_run_bakeoff_elegance_skipped_when_judge_unconfigured(tmp_path):
    # A16: judge gateway (zen) unconfigured -> judging skipped, models STILL reported, elegance None.
    import asyncio
    task = _trivial_task(tmp_path)
    go_only = {"BAKEOFF_OPENCODE_GO_URL": "https://go/v1", "BAKEOFF_OPENCODE_GO_KEY": "k"}
    result, _ = asyncio.run(cli.run_bakeoff(
        [registry.by_key("deepseek-v4-flash")], [task], go_only, str(tmp_path / "r"), 10.0, 1,
        _combo_transport(), bar=0.0))
    rows = {r.model_key: r for r in result.report_rows}
    assert rows["deepseek-v4-flash"].mean_elegance is None
    assert rows["deepseek-v4-flash"].n_elegance_judged == 0
    assert any("elegance skipped" in n for n in result.notes)


def test_run_bakeoff_judge_enabled_default_true_still_judges(tmp_path):
    # Task 7 REGRESSION pin: the default judge_enabled=True preserves today's Phase-2 behaviour.
    import asyncio
    task = _trivial_task(tmp_path)
    result, _ = asyncio.run(cli.run_bakeoff(
        [registry.by_key("deepseek-v4-flash")], [task], _FULL_ENV, str(tmp_path / "r"), 10.0, 1,
        _combo_transport(), bar=0.0))                # judge_enabled defaults True
    rows = {r.model_key: r for r in result.report_rows}
    assert rows["deepseek-v4-flash"].mean_elegance == 0.75    # Phase 2 ran by default


def test_run_bakeoff_judge_enabled_false_is_a_genuine_noop(tmp_path):
    # Task 7: judge_enabled=False skips the self-grade guard, the judge gateway resolve, AND Phase 2.
    import asyncio
    import dataclasses
    task = _trivial_task(tmp_path)
    # a SAME-FAMILY judge that WOULD trip the self-grade ValueError if judging were enabled
    jspec = dataclasses.replace(registry.judge_spec(), key="deepseek-judge", wire_id="deepseek-judge")
    judge_called = {"n": 0}

    async def transport(url, headers, json, timeout):
        prompt = json["messages"][0]["content"]
        if "UNTRUSTED" in prompt:
            judge_called["n"] += 1
            return 200, {"choices": [{"message": {"content": '{"elegance":0.9,"rationale":"x"}'}}],
                         "usage": {"prompt_tokens": 10, "completion_tokens": 5}}, 0.2
        return 200, {"choices": [{"message": {"content": "```python\ndef f():\n    return 1\n```"}}],
                     "usage": {"prompt_tokens": 10, "completion_tokens": 5}}, 0.2

    result, _ = asyncio.run(cli.run_bakeoff(
        [registry.by_key("deepseek-v4-flash")], [task], _FULL_ENV, str(tmp_path / "r"), 10.0, 1,
        transport, bar=0.0, judge_spec=jspec, judge_enabled=False))   # no raise despite same family
    rows = {r.model_key: r for r in result.report_rows}
    assert rows["deepseek-v4-flash"].mean_elegance is None        # Phase 2 skipped
    assert rows["deepseek-v4-flash"].n_elegance_judged == 0
    assert judge_called["n"] == 0                                 # NO judge call made
    assert rows["deepseek-v4-flash"].n_passed == 1               # non-elegance output intact
    assert any("phase judging disabled" in n for n in result.notes)


def test_run_bakeoff_budget_exhausted_mid_judging_leaves_later_model_unjudged(tmp_path):
    # A19: budget below one judge cell -> first queued model judged then budget trips; later model
    # never reached -> still present with n_elegance_judged==0 (not dropped).
    import asyncio
    task = _trivial_task(tmp_path)
    models = [registry.by_key("deepseek-v4-flash"), registry.by_key("glm-5.1")]
    result, _ = asyncio.run(cli.run_bakeoff(models, [task], _FULL_ENV, str(tmp_path / "r"),
                                            0.0003, 1, _combo_transport(), bar=0.0))
    rows = {r.model_key: r for r in result.report_rows}
    assert rows["deepseek-v4-flash"].n_elegance_judged == 1
    assert rows["glm-5.1"].n_elegance_judged == 0
    assert rows["glm-5.1"].mean_elegance is None
    assert any("judge budget exhausted" in n for n in result.notes)


def test_run_bakeoff_per_repeat_raw_files_no_collision(tmp_path):
    # A13: repeats=2 must yield n_models*n_tasks*repeats distinct raw files (not last-write-wins).
    import asyncio
    from tools.model_bakeoff.models import TaskSpec
    tasks = []
    for tid in ("a", "b"):
        (tmp_path / f"{tid}p.md").write_text("write f returning 1")
        (tmp_path / f"{tid}o_test.py").write_text(
            "from solution import f\n\ndef test_f():\n    assert f() == 1\n")
        (tmp_path / f"{tid}r.py").write_text("def f():\n    return 1\n")
        tasks.append(TaskSpec(tid, "quick", str(tmp_path / f"{tid}p.md"),
                              str(tmp_path / f"{tid}o_test.py"), str(tmp_path / f"{tid}r.py")))
    out = str(tmp_path / "r")
    asyncio.run(cli.run_bakeoff([registry.by_key("deepseek-v4-flash")], tasks, _FULL_ENV,
                                out, 10.0, 2, _combo_transport(), bar=0.0))
    raw = set(os.listdir(os.path.join(out, "raw")))
    assert len(raw) == 1 * 2 * 2
    assert {"deepseek-v4-flash__a__r0.json", "deepseek-v4-flash__a__r1.json"} <= raw


def test_run_bakeoff_ceiling_phantom_guard(tmp_path):
    # A4 step 5: ceiling_on but ceiling absent from this run -> omit it + WARNING (no phantom entry).
    import asyncio
    task = _trivial_task(tmp_path)
    result, _ = asyncio.run(cli.run_bakeoff([registry.by_key("deepseek-v4-flash")], [task],
                                            _FULL_ENV, str(tmp_path / "r"), 10.0, 1,
                                            _combo_transport(), bar=0.0, ceiling_on=True))
    assert "claude-opus-4-8" not in result.ladder
    assert any("ceiling" in n and "not in this run" in n for n in result.notes)


def test_run_parser_has_judge_and_no_ceiling_flags():
    p = cli.build_parser()
    assert p.parse_args(["run"]).judge == registry.judge_spec().key
    assert p.parse_args(["run"]).no_ceiling is False
    assert p.parse_args(["run", "--no-ceiling"]).no_ceiling is True


# --- Sub-project D Task 9: estimate --dualrun ---

def test_dualrun_estimate_judge_spend_scales_with_policy():
    tasks = corpus.load(selector="quick")
    models = [registry.by_key("deepseek-v4-flash")]
    _, jb, _, _ = cli._dualrun_estimate(models, tasks, 1, "both")
    _, jbs, _, _ = cli._dualrun_estimate(models, tasks, 1, "bestshot")
    _, jn, _, _ = cli._dualrun_estimate(models, tasks, 1, "none")
    assert jn == 0.0 and jbs > 0.0 and abs(jb - 2 * jbs) < 1e-12     # both=2x, bestshot=1x, none=0


def test_dualrun_estimate_metered_candidate_spend_doubles_and_names_metered():
    tasks = corpus.load(selector="quick")
    sub_total, _, sub_metered, _ = cli._dualrun_estimate([registry.by_key("glm-5.1")], tasks, 1, "none")
    assert sub_total == 0.0 and sub_metered == []                    # subscription -> zero, none named
    _, single_total, _ = cli.estimate([registry.by_key("claude-opus-4-8")], tasks, 1)
    dual_total, _, metered, _ = cli._dualrun_estimate([registry.by_key("claude-opus-4-8")], tasks, 1, "none")
    assert single_total > 0 and dual_total == 2 * single_total       # x2 phases
    assert metered == ["claude-opus-4-8"]                            # metered candidate named


def test_dualrun_estimate_flags_noisy_models_for_repeats():
    tasks = corpus.load(selector="quick")
    _, _, _, noisy = cli._dualrun_estimate([registry.by_key("deepseek-v4-flash")], tasks, 1, "bestshot")
    assert "deepseek-v4-flash" in noisy                              # reasoning + omit_temp -> flagged
    _, _, _, noisy2 = cli._dualrun_estimate([registry.by_key("glm-5.1")], tasks, 1, "bestshot")
    assert noisy2 == []                                              # non-reasoning, omit_temp False


def test_estimate_dualrun_parser_and_output(capsys):
    a = cli.build_parser().parse_args(["estimate", "--dualrun", "--elegance", "both"])
    assert a.dualrun is True and a.elegance == "both"
    cli.cmd_estimate(SimpleNamespace(models="deepseek-v4-flash", repeats=1, budget=100.0,
                                     suite="quick", dualrun=True, elegance="both"))
    out = capsys.readouterr().out
    assert "dual-run estimate" in out
    assert "projected judge spend" in out and "x2" in out
    assert "--repeats>=3" in out                                     # noisy-model guidance


def test_estimate_projects_judge_spend_and_ci_width(capsys):
    import re as _re
    cli.cmd_estimate(SimpleNamespace(models="deepseek-v4-flash", repeats=3, budget=10.0))
    out = capsys.readouterr().out
    assert "projected judge spend:" in out
    assert "Wilson 95% CI half-width" in out
    m = _re.search(r"projected judge spend: \$([0-9.]+)", out)
    assert m and float(m.group(1)) > 0


# --- Sub-project D Task 8: dualrun CLI (interleaved baseline vs best-shot, shared budget, leakage) ---

def _dualrun_args(**kw):
    base = dict(models="", suite=None, settings_dir=None, tasks_dir=None, dev_tasks_dir=None,
                budget=10.0, elegance="bestshot", order="alternate", judge=registry.judge_spec().key,
                no_ceiling=False, repeats=1, bar=0.0, sandbox_timeout=30, out=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _write_best_settings(settings_dir, spec, winner):
    import dataclasses as _dc
    d = os.path.join(str(settings_dir), spec.key)
    os.makedirs(d, exist_ok=True)
    rec = {"schema_version": 1, "model_key": spec.key, "base": _dc.asdict(spec),
           "winner": _dc.asdict(winner), "low_confidence": False, "reasons": [], "caveats": []}
    with open(os.path.join(d, "best_settings.json"), "w") as f:
        json.dump(rec, f)


def _dualrun_combo(solution="def f(x):\n    return x + 1",
                   judge_content='{"elegance": 0.75, "rationale": "clean"}',
                   in_tok=10, out_tok=5, judge_in=100, judge_out=20):
    """One transport for both roles: a judge call (prompt carries 'UNTRUSTED') returns judge JSON; any
    other call returns a candidate solution that passes make_cli_task's oracle (f(2)==3)."""
    async def t(url, headers, json, timeout):
        prompt = json["messages"][0]["content"]
        if "UNTRUSTED" in prompt:
            return 200, {"choices": [{"message": {"content": judge_content}}],
                         "usage": {"prompt_tokens": judge_in, "completion_tokens": judge_out}}, 0.2
        return 200, {"choices": [{"message": {"content": f"```python\n{solution}\n```"}}],
                     "usage": {"prompt_tokens": in_tok, "completion_tokens": out_tok}}, 0.2
    return t


def test_dualrun_happy_path_writes_incremental_artefacts(tmp_path):
    from tools.model_bakeoff.models import SettingsProfile
    scored = tmp_path / "scored"
    make_cli_task(scored, "quick-a")
    settings = tmp_path / "settings"
    _write_best_settings(settings, registry.by_key("glm-5.1"), SettingsProfile(max_tokens=16000))
    out = tmp_path / "out"
    rc = cli.cmd_dualrun(_dualrun_args(models="glm-5.1", tasks_dir=str(scored),
                                       settings_dir=str(settings), out=str(out)),
                         env=_FULL_ENV, transport=_dualrun_combo())
    assert rc == 0
    assert os.path.isdir(os.path.join(str(out), "glm-5.1", "baseline"))    # per-(model,phase) subdirs
    assert os.path.isdir(os.path.join(str(out), "glm-5.1", "bestshot"))
    assert os.path.exists(os.path.join(str(out), "dualrun.md"))
    data = json.load(open(os.path.join(str(out), "dualrun_summary.json")))
    assert data["models"][0]["model_key"] == "glm-5.1"
    assert data["models"][0]["paired"] is not None                        # paired p-value present
    assert "paired McNemar" in open(os.path.join(str(out), "dualrun.md")).read()


def test_dualrun_elegance_bestshot_spends_half_of_both(tmp_path, capsys):
    import re as _re
    scored = tmp_path / "scored"
    make_cli_task(scored, "quick-a")
    settings = tmp_path / "settings"
    os.makedirs(str(settings), exist_ok=True)

    def _spend(policy):
        out = tmp_path / f"out-{policy}"
        cli.cmd_dualrun(_dualrun_args(models="glm-5.1", tasks_dir=str(scored),
                                      settings_dir=str(settings), elegance=policy, out=str(out)),
                        env=_FULL_ENV, transport=_dualrun_combo())
        return float(_re.search(r"metered spend \$([0-9.]+)", capsys.readouterr().out).group(1))

    both, bestshot = _spend("both"), _spend("bestshot")
    assert both > 0 and abs(bestshot * 2 - both) < 1e-9    # both judges 2 phases, bestshot judges 1


def test_dualrun_leakage_dir_pair_exits_2(tmp_path, capsys):
    scored = tmp_path / "scored"
    make_cli_task(scored, "quick-x")
    dev = tmp_path / "dev"
    make_cli_task(dev, "quick-x")                          # SAME id in dev -> leakage
    settings = tmp_path / "settings"
    os.makedirs(str(settings), exist_ok=True)
    rc = cli.cmd_dualrun(_dualrun_args(models="glm-5.1", tasks_dir=str(scored), dev_tasks_dir=str(dev),
                                       settings_dir=str(settings)),
                         env=_FULL_ENV, transport=_boom_transport)
    assert rc == 2 and "leak" in capsys.readouterr().out.lower()


def test_dualrun_leakage_dev_corpus_json_fallback_exits_2(tmp_path):
    scored = tmp_path / "scored"
    make_cli_task(scored, "quick-x")
    settings = tmp_path / "settings"
    os.makedirs(str(settings), exist_ok=True)
    with open(os.path.join(str(settings), "dev_corpus.json"), "w") as f:
        json.dump({"tree_sha": "abc", "oracle_ref_sha256": "def", "dev_tasks": ["quick-x"]}, f)
    rc = cli.cmd_dualrun(_dualrun_args(models="glm-5.1", tasks_dir=str(scored),
                                       settings_dir=str(settings)),      # no dev dir -> json ids used
                         env=_FULL_ENV, transport=_boom_transport)
    assert rc == 2


def test_dualrun_no_tuned_record_runs_baseline_both_phases(tmp_path):
    scored = tmp_path / "scored"
    make_cli_task(scored, "quick-a")
    settings = tmp_path / "settings"
    os.makedirs(str(settings), exist_ok=True)
    out = tmp_path / "out"
    rc = cli.cmd_dualrun(_dualrun_args(models="glm-5.1", tasks_dir=str(scored),
                                       settings_dir=str(settings), elegance="none", out=str(out)),
                         env=_FULL_ENV, transport=_dualrun_combo())
    assert rc == 0
    m = json.load(open(os.path.join(str(out), "dualrun_summary.json")))["models"][0]
    assert m["pass_fraction_delta"] == 0.0                 # identical spec both phases -> zero delta
    assert "no tuned record for glm-5.1" in open(os.path.join(str(out), "dualrun.md")).read()


def test_dualrun_default_models_excludes_metered():
    keys = {m.key for m in cli._dualrun_default_models("")}
    assert not ({"claude-opus-4-8", "minimax-m3", "qwen3.7-max"} & keys)   # all 3 metered excluded
    assert "glm-5.1" in keys


def test_dualrun_explicit_metered_prints_loud_note(tmp_path, capsys):
    scored = tmp_path / "scored"
    make_cli_task(scored, "quick-a")
    settings = tmp_path / "settings"
    os.makedirs(str(settings), exist_ok=True)
    rc = cli.cmd_dualrun(_dualrun_args(models="minimax-m3", tasks_dir=str(scored),
                                       settings_dir=str(settings), elegance="none",
                                       out=str(tmp_path / "out")),
                         env=_FULL_ENV, transport=_dualrun_combo())
    assert rc == 0
    assert "metered model(s) selected" in capsys.readouterr().out


def test_dualrun_unconfigured_gateway_excludes_model_others_run(tmp_path):
    scored = tmp_path / "scored"
    make_cli_task(scored, "quick-a")
    settings = tmp_path / "settings"
    os.makedirs(str(settings), exist_ok=True)
    out = tmp_path / "out"
    go_only = {"BAKEOFF_OPENCODE_GO_URL": "https://go/v1", "BAKEOFF_OPENCODE_GO_KEY": "k"}
    rc = cli.cmd_dualrun(_dualrun_args(models="glm-5.1,qwen3.5-397b", tasks_dir=str(scored),
                                       settings_dir=str(settings), elegance="none", out=str(out)),
                         env=go_only, transport=_dualrun_combo())     # ollama-cloud unconfigured
    assert rc == 0
    data = json.load(open(os.path.join(str(out), "dualrun_summary.json")))
    keys = {m["model_key"] for m in data["models"]}
    assert "glm-5.1" in keys and "qwen3.5-397b" not in keys           # unconfigured excluded, other ran
    assert any("excluded qwen3.5-397b" in n for n in data["notes"])


def test_dualrun_order_alternate_even_odd_differ(tmp_path):
    scored = tmp_path / "scored"
    make_cli_task(scored, "quick-a")
    settings = tmp_path / "settings"
    os.makedirs(str(settings), exist_ok=True)
    out = tmp_path / "out"
    rc = cli.cmd_dualrun(_dualrun_args(models="glm-5.1,glm-5.2", tasks_dir=str(scored),
                                       settings_dir=str(settings), elegance="none",
                                       order="alternate", out=str(out)),
                         env=_FULL_ENV, transport=_dualrun_combo())
    assert rc == 0
    orders = {m["model_key"]: m["order"]
              for m in json.load(open(os.path.join(str(out), "dualrun_summary.json")))["models"]}
    assert orders["glm-5.1"] == "baseline-first"      # index 0 (even)
    assert orders["glm-5.2"] == "bestshot-first"      # index 1 (odd)


def test_dualrun_shared_budget_truncates_safely(tmp_path, capsys):
    import re as _re
    scored = tmp_path / "scored"
    make_cli_task(scored, "quick-a")
    make_cli_task(scored, "quick-b")                  # 2 scored tasks
    settings = tmp_path / "settings"
    os.makedirs(str(settings), exist_ok=True)
    out = tmp_path / "out"
    # claude-opus-4-8 is metered + priced (5/25); out_tok=40000 -> per-task cost = 40000*25/1e6 = $1.0
    tx = _dualrun_combo(in_tok=0, out_tok=40000)
    budget = 1.5
    rc = cli.cmd_dualrun(_dualrun_args(models="claude-opus-4-8", tasks_dir=str(scored),
                                       settings_dir=str(settings), elegance="none",
                                       budget=budget, out=str(out)),
                         env=_FULL_ENV, transport=tx)
    assert rc == 0
    spent = float(_re.search(r"metered spend \$([0-9.]+)", capsys.readouterr().out).group(1))
    assert spent <= budget + 2 * 1.0 + 1e-9           # subtraction bounds it; each phase overshoots <= 1 op
    data = json.load(open(os.path.join(str(out), "dualrun_summary.json")))
    assert any("BUDGET STOP" in n for n in data["notes"])
    m = data["models"][0]
    assert m["no_data"] or m["task_composition_mismatch"] or m["empty_intersection"]
    assert not (m["paired"] and m["paired"]["significant"])   # truncation must never fabricate significance


def test_dualrun_parser_wires_flags():
    a = cli.build_parser().parse_args(["dualrun", "--settings-dir", "/s", "--elegance", "both",
                                       "--order", "bestshot-first", "--repeats", "3"])
    assert a.func is cli.cmd_dualrun and a.settings_dir == "/s"
    assert a.elegance == "both" and a.order == "bestshot-first" and a.repeats == 3


# --- Sub-project C Task 6: tune CLI (subscription-only) + leakage guard + --try-gateway ---

def make_cli_task(tasks_dir, task_id="quick-dev1"):
    # directory-per-task layout that corpus.load discovers: <tasks_dir>/<task_id>/{prompt,oracle,reference}
    d = os.path.join(str(tasks_dir), task_id)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "prompt.md"), "w") as f:
        f.write("Implement f(x) returning x+1 in solution.py.")
    with open(os.path.join(d, "oracle.py"), "w") as f:
        f.write("from solution import f\n\ndef test_f():\n    assert f(2) == 3\n")
    with open(os.path.join(d, "reference.py"), "w") as f:
        f.write("def f(x):\n    return x + 1\n")
    return d


_ENV_GO = {"BAKEOFF_OPENCODE_GO_URL": "https://go/v1", "BAKEOFF_OPENCODE_GO_KEY": "k"}


async def _boom_transport(url, headers, json, timeout):
    raise AssertionError("transport must not be called")


def _tune_passing_transport():
    async def t(url, headers, json, timeout):
        return 200, {"choices": [{"message": {"content": "```python\ndef f(x):\n    return x + 1\n```"}}],
                     "usage": {"prompt_tokens": 10, "completion_tokens": 50}}, 0.2
    return t


def _tune_args(**kw):
    base = dict(models="", suite=None, against=None, tasks_dir=None, suites_dir=None,
                try_gateway=None, repeats=1, out=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_tune_refuses_overlapping_dev_and_scored(tmp_path, capsys):
    rc = cli.cmd_tune(_tune_args(models="glm-5.1", suite="tag:ai-trap", against="all",
                                 out=str(tmp_path)), env=_ENV_GO, transport=_boom_transport)
    assert rc == 2 and "disjoint" in capsys.readouterr().out.lower()


def test_tune_refuses_metered_model(tmp_path, capsys):
    rc = cli.cmd_tune(_tune_args(models="claude-opus-4-8", suite="tag:ai-trap", out=str(tmp_path)),
                      env=_ENV_GO, transport=_boom_transport)
    assert rc == 2 and "metered" in capsys.readouterr().out.lower()
    assert os.path.exists(os.path.join(str(tmp_path), "SKIPPED.json"))


def test_tune_writes_best_settings_for_subscription_model(tmp_path):
    make_cli_task(tmp_path / "corpus")
    out = tmp_path / "out"
    rc = cli.cmd_tune(_tune_args(models="glm-5.1", suite=None, tasks_dir=str(tmp_path / "corpus"),
                                 out=str(out)), env=_ENV_GO, transport=_tune_passing_transport())
    assert rc == 0 and (out / "glm-5.1" / "best_settings.json").exists()


def test_tune_try_gateway_metered_is_pruned(tmp_path):
    make_cli_task(tmp_path / "corpus")
    out = tmp_path / "out"
    rc = cli.cmd_tune(_tune_args(models="glm-5.1", suite=None, tasks_dir=str(tmp_path / "corpus"),
                                 try_gateway=["opencode-zen:x"], out=str(out)),
                      env=_ENV_GO, transport=_tune_passing_transport())
    assert rc == 0
    rec = json.load(open(os.path.join(str(out), "glm-5.1", "best_settings.json")))
    assert any("metered" in n.lower() for n in rec["notes"])   # metered gateway dropped, no metered call


def test_tune_parse_try_gateways_colon_wire_id():
    # real ollama wire_ids contain colons; split(":", 1) must keep them intact
    assert cli._parse_try_gateways(["ollama-cloud:qwen3.5:397b"]) == [("ollama-cloud", "qwen3.5:397b")]


def test_tune_parser_wires_flags():
    a = cli.build_parser().parse_args(["tune", "--suite", "dev", "--against", "all",
                                       "--try-gateway", "a:b", "--try-gateway", "c:d"])
    assert a.suite == "dev" and a.against == "all" and a.func is cli.cmd_tune
    assert a.try_gateway == ["a:b", "c:d"] and a.repeats == 2
