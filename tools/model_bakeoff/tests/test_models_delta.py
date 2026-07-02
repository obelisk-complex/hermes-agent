"""Task 1: data-model fields for the coding bakeoff (elegance + cost proxy + per-repeat).

Locks in the DEFAULTS of the additive fields so every existing constructor keeps working
(ModelSpec/ModelAggregate are frozen; TaskRun is a plain mutable dataclass).
"""
from tools.model_bakeoff.models import CallResult, ModelAggregate, ScoreResult
from tools.model_bakeoff.runner import TaskRun


def test_model_aggregate_new_fields_default():
    agg = ModelAggregate(
        model_key="m", reasoning=False, cost_model="subscription", n_tasks=3, n_passed=2)
    assert agg.mean_elegance is None
    assert agg.cost_proxy_per_task_usd == 0.0
    assert agg.n_elegance_judged == 0
    assert agg.n_latency_samples == 0


def test_task_run_new_fields_default():
    call = CallResult(model_key="m", task_id="t", ok=True)
    score = ScoreResult(model_key="m", task_id="t", passed=True)
    tr = TaskRun(call=call, score=score)
    assert tr.elegance is None
    assert tr.elegance_rationale == ""
    assert tr.judge_cost_usd == 0.0
    assert tr.repeat_idx == 0


def test_task_run_repeat_idx_is_mutable():
    """run_bakeoff stamps repeat_idx post-construction (A13); the dataclass must allow it."""
    tr = TaskRun(
        call=CallResult(model_key="m", task_id="t", ok=True),
        score=ScoreResult(model_key="m", task_id="t", passed=True))
    tr.repeat_idx = 2
    assert tr.repeat_idx == 2
