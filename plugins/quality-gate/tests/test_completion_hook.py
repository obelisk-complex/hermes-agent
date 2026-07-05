import sys

import completion_hook
import gate
import registry


def _task(tmp, **over):
    base = dict(id="t-1", workspace_path=str(tmp))
    base.update(over)
    return base


def test_passing_gate_allows(tmp_workspace, monkeypatch):
    (tmp_workspace / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    monkeypatch.setattr(
        registry, "DEFAULT_GATES",
        {"python": {"lint": [], "test": [[sys.executable, "-c", "print('ok')"]]}},
    )
    out = completion_hook.on_pre_kanban_complete(task=_task(tmp_workspace), config={})
    assert out is None  # allowed


def test_failing_gate_blocks(tmp_workspace, monkeypatch):
    (tmp_workspace / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    monkeypatch.setattr(
        registry, "DEFAULT_GATES",
        {"python": {"lint": [], "test": [[sys.executable, "-c", "import sys; sys.exit(1)"]]}},
    )
    out = completion_hook.on_pre_kanban_complete(task=_task(tmp_workspace), config={})
    assert out["action"] == "block"
    assert "FAIL" in out["message"]


def test_no_workspace_allows_but_warns(tmp_workspace, caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        out = completion_hook.on_pre_kanban_complete(task={"id": "t-1"}, config={})
    assert out is None  # no workspace -> allow
    assert any(
        r.levelno >= logging.WARNING and "workspace_path" in r.message
        for r in caplog.records
    ), "expected a WARNING mentioning workspace_path when workspace is absent"


def test_internal_error_blocks_fail_closed(tmp_workspace, monkeypatch):
    (tmp_workspace / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

    def boom(*a, **k):
        raise RuntimeError("gate exploded")

    monkeypatch.setattr(gate, "evaluate_completion", boom)
    out = completion_hook.on_pre_kanban_complete(task=_task(tmp_workspace), config={})
    assert out["action"] == "block"
    assert "could not be evaluated" in out["message"]


def test_uses_tier_sidecar(tmp_workspace, monkeypatch):
    import classify
    classify.write_tier(tmp_workspace, "quick")
    seen = {}

    def fake_eval(ws, tier, *, task_id="", **kw):
        seen["tier"] = tier
        return gate.GateResult(passed=True, summary="ok")

    monkeypatch.setattr(gate, "evaluate_completion", fake_eval)
    completion_hook.on_pre_kanban_complete(task=_task(tmp_workspace), config={})
    assert seen["tier"] == "quick"


def test_scratch_workspace_with_code_is_gated(tmp_workspace, monkeypatch):
    # workspace_kind="scratch" is the kanban DEFAULT. A scratch dir containing
    # code must NOT be skipped on kind -- the hook must run the gate and block on
    # failure exactly as for a worktree/dir card.
    (tmp_workspace / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    monkeypatch.setattr(
        registry, "DEFAULT_GATES",
        {"python": {"lint": [], "test": [[sys.executable, "-c", "import sys; sys.exit(1)"]]}},
    )
    out = completion_hook.on_pre_kanban_complete(
        task=_task(tmp_workspace, workspace_kind="scratch"), config={},
    )
    assert out["action"] == "block"


def test_verdict_logged_for_audit(tmp_workspace, monkeypatch, caplog):
    # The durable verdict line must be emitted (it is the only audit trail that
    # survives a scratch-dir cleanup).
    import logging
    def fake_eval(ws, tier, *, task_id="", **kw):
        return gate.GateResult(passed=True, summary="ok", stacks=["python"], tier="standard")
    monkeypatch.setattr(gate, "evaluate_completion", fake_eval)
    with caplog.at_level(logging.INFO):
        completion_hook.on_pre_kanban_complete(task=_task(tmp_workspace), config={})
    assert any("quality-gate VERDICT" in r.message for r in caplog.records)
