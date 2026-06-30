"""runner orchestration: budget, warm-up, and the full call->score pipeline
(SPEC §5, §8, §10). Offline: fake transport + real sandbox subprocess."""
from __future__ import annotations

import asyncio

import pytest

from tools.model_bakeoff import runner
from tools.model_bakeoff.models import ERR_CALL, ModelSpec, TaskSpec

SUB = ModelSpec(key="sub", gateway="opencode-go", wire_id="deepseek-v4-flash",
                cost_model="subscription", reasoning=True, omit_temp=True,
                max_tokens=16000, api_timeout_s=240)
ZEN = ModelSpec(key="opus", gateway="opencode-zen", wire_id="claude-opus-4-8",
                cost_model="metered", reasoning=False, omit_temp=False,
                max_tokens=8000, api_timeout_s=180, is_ceiling=True,
                price_in_per_m=5.0, price_out_per_m=25.0)

ORACLE = "from solution import f\n\ndef test_f():\n    assert f(2) == 3\n"


def make_task(tmp_path, tid, oracle_body=ORACLE):
    (tmp_path / f"{tid}_p.md").write_text("Implement `f(x)` returning x+1 in solution.py.")
    (tmp_path / f"{tid}_o.py").write_text(oracle_body)
    (tmp_path / f"{tid}_r.py").write_text("def f(x):\n    return x + 1\n")
    return TaskSpec(tid, "quick", str(tmp_path / f"{tid}_p.md"),
                    str(tmp_path / f"{tid}_o.py"), str(tmp_path / f"{tid}_r.py"))


def transport_returning(code, usage=None):
    async def t(url, headers, json, timeout):
        return 200, {"choices": [{"message": {"content": code}}],
                     "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5}}, 0.2
    return t


def test_budget_tracker_accumulates_and_raises():
    b = runner.BudgetTracker(0.10)
    b.add(0.05)
    assert abs(b.remaining() - 0.05) < 1e-9
    with pytest.raises(runner.BudgetExceeded):
        b.add(0.06)


def test_should_rewarm_only_when_second_much_slower():
    assert runner.should_rewarm(1.0, 2.5)
    assert not runner.should_rewarm(1.0, 1.5)
    assert not runner.should_rewarm(None, 9.0)


def test_run_one_pass_end_to_end(tmp_path):
    task = make_task(tmp_path, "t1")
    t = transport_returning("```python\ndef f(x):\n    return x + 1\n```")
    tr = asyncio.run(runner.run_one(SUB, task, "K", "https://b/v1", t, sandbox_timeout=30))
    assert tr.score.passed
    assert tr.call.extracted_code.startswith("def f")


def test_run_one_wrong_answer_is_test_failure(tmp_path):
    task = make_task(tmp_path, "t2")
    t = transport_returning("```python\ndef f(x):\n    return x + 99\n```")
    tr = asyncio.run(runner.run_one(SUB, task, "K", "https://b/v1", t, sandbox_timeout=30))
    assert not tr.score.passed
    assert tr.score.error_type == "test_failure"


def test_run_one_transport_failure_is_call_error(tmp_path):
    task = make_task(tmp_path, "t3")

    async def boom(url, headers, json, timeout):
        raise ConnectionError("refused")

    tr = asyncio.run(runner.run_one(SUB, task, "K", "https://b/v1", boom, sandbox_timeout=30))
    assert not tr.score.passed
    assert tr.score.error_type == ERR_CALL


def test_run_model_warms_up_then_aggregates(tmp_path):
    task = make_task(tmp_path, "t4")
    t = transport_returning("```python\ndef f(x):\n    return x + 1\n```")
    runs, warmups = asyncio.run(runner.run_model(SUB, [task], "K", "https://b/v1", t, sandbox_timeout=30))
    assert len(warmups) == 2  # equal latencies => no re-warm
    agg = runner.aggregate(SUB, runs)
    assert agg.n_tasks == 1 and agg.n_passed == 1
    assert agg.pass_fraction == 1.0


def test_metered_budget_enforced_during_run(tmp_path):
    task = make_task(tmp_path, "t5")
    huge = {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}
    t = transport_returning("```python\ndef f(x):\n    return x + 1\n```", usage=huge)
    b = runner.BudgetTracker(0.01)
    with pytest.raises(runner.BudgetExceeded):
        asyncio.run(runner.run_model(ZEN, [task], "K", "https://b/v1", t, budget=b, sandbox_timeout=30))


def test_subscription_run_costs_nothing(tmp_path):
    task = make_task(tmp_path, "t6")
    huge = {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}
    t = transport_returning("```python\ndef f(x):\n    return x + 1\n```", usage=huge)
    runs, _ = asyncio.run(runner.run_model(SUB, [task], "K", "https://b/v1", t, sandbox_timeout=30))
    assert runner.aggregate(SUB, runs).cost_per_task_usd == 0.0


def test_run_one_threads_reasoning_extras_into_payload(tmp_path):
    task = make_task(tmp_path, "tre")
    captured = {}

    async def cap(url, headers, json, timeout):
        captured["payload"] = json
        return 200, {"choices": [{"message": {"content": "```python\ndef f(x):\n    return x + 1\n```"}}],
                     "usage": {"prompt_tokens": 1, "completion_tokens": 1}}, 0.2

    ds = ModelSpec(key="ds", gateway="opencode-go", wire_id="deepseek-v4-pro",
                   cost_model="subscription", reasoning=True, omit_temp=True,
                   max_tokens=16000, api_timeout_s=240,
                   reasoning_extras={"thinking": {"type": "enabled"}})
    asyncio.run(runner.run_one(ds, task, "K", "https://b/v1", cap, sandbox_timeout=30))
    assert captured["payload"].get("thinking") == {"type": "enabled"}

    asyncio.run(runner.run_one(SUB, task, "K", "https://b/v1", cap, sandbox_timeout=30))
    assert "thinking" not in captured["payload"]   # SUB carries no reasoning_extras


def test_run_one_sandbox_timeout_defaults_to_60_not_api_timeout(tmp_path, monkeypatch):
    from tools.model_bakeoff.models import SandboxResult
    task = make_task(tmp_path, "tto")
    captured = {}

    def fake_sandbox_run(code, oracle_path, timeout_s=60, **kw):
        captured["timeout_s"] = timeout_s
        return SandboxResult(returncode=0, stdout="1 passed")

    monkeypatch.setattr(runner.sandbox, "run", fake_sandbox_run)
    t = transport_returning("```python\ndef f(x):\n    return x + 1\n```")
    # SUB.api_timeout_s == 240; run_one with no sandbox_timeout must fall back to 60, not 240.
    asyncio.run(runner.run_one(SUB, task, "K", "https://b/v1", t))
    assert captured["timeout_s"] == 60


def test_run_model_attaches_partial_runs_on_budget_stop(tmp_path):
    # M3: a budget-stop must carry the completed task run(s) out on the exception so the caller
    # can persist/count them (SPEC §8 partials persisted).
    task = make_task(tmp_path, "tb")
    huge = {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}
    t = transport_returning("```python\ndef f(x):\n    return x + 1\n```", usage=huge)
    b = runner.BudgetTracker(0.01)
    with pytest.raises(runner.BudgetExceeded) as ei:
        asyncio.run(runner.run_model(ZEN, [task], "K", "https://b/v1", t, budget=b, sandbox_timeout=30))
    assert len(ei.value.partial_runs) >= 1
    assert ei.value.partial_runs[0].call.task_id == "tb"
