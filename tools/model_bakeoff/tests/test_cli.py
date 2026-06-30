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
