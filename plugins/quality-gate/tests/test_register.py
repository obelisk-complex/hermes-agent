import pytest

# real_load_plugin loads the entry the way the plugin manager does -- under the
# REAL package name hermes_plugins.quality_gate, NO sys.path insert -- so:
#   * the entry's ``from . import completion_hook`` (and every sibling's
#     ``from . import registry`` etc.) MUST resolve relatively, proving the
#     production import wiring (a flat sibling import would ModuleNotFoundError);
#   * the loaded siblings are the SAME objects the conftest aliased to bare
#     names, so ``import registry; registry.DEFAULT_GATES = ...`` patches what
#     the gate actually reads (no module-identity split -- the audit's concern).
from conftest import real_load_plugin


def _load_entry():
    return real_load_plugin("hermes_plugins.quality_gate")


class _Ctx:
    def __init__(self):
        self.hooks = {}
    def register_hook(self, name, cb):
        self.hooks[name] = cb
    @property
    def llm(self):
        return None


def test_register_wires_three_kanban_hooks():
    entry = _load_entry()
    ctx = _Ctx()
    entry.register(ctx)
    assert set(ctx.hooks) == {
        "pre_kanban_spawn", "kanban_task_blocked", "pre_kanban_complete",
    }


def test_entry_uses_relative_sibling_imports():
    # Loading under the real package name with NO sys.path insert would raise
    # ModuleNotFoundError if any sibling used a flat ``import X`` -- so a clean
    # load here IS the proof the import discipline holds end to end.
    entry = _load_entry()
    assert entry.__name__ == "hermes_plugins.quality_gate"
    # The entry pulled in its siblings relatively.
    assert hasattr(entry, "completion_hook")
    assert hasattr(entry, "spawn_hook")
    assert hasattr(entry, "blocked_hook")


def test_load_config_degrades_to_empty():
    entry = _load_entry()
    # _load_config must never raise even if the loader blows up.
    cfg = entry._load_config()
    assert isinstance(cfg, dict)


def test_completion_adapter_blocks_on_failing_gate(tmp_path, monkeypatch):
    import sys
    import gate
    entry = _load_entry()
    ctx = _Ctx()
    entry.register(ctx)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    # Monkeypatch gate.evaluate_completion to a failing result, mirroring the
    # fail-closed completion test (more robust than patching DEFAULT_GATES,
    # which the audit flagged as identity-fragile). The completion_hook the
    # entry imported is the SAME module object as bare ``gate`` (conftest alias).
    def fake_eval(workspace, tier, *, task_id="", **kw):
        return gate.GateResult(passed=False, summary="quality-gate [standard]: FAIL\n  FAIL python/test")
    monkeypatch.setattr(gate, "evaluate_completion", fake_eval)
    out = ctx.hooks["pre_kanban_complete"](task={"id": "t-1", "workspace_path": str(ws)})
    assert out["action"] == "block"
    assert "FAIL" in out["message"]


def test_complete_closure_is_fail_closed_on_crash(tmp_path, monkeypatch):
    # The closure's OWN try/except (inside completion_hook) must convert a gate
    # crash into a BLOCK dict -- even though invoke_hook would otherwise swallow a
    # raise and fail OPEN (allow completion). We invoke the registered closure
    # directly the way invoke_hook would call it; it must RETURN a block dict,
    # never propagate the exception.
    import gate
    entry = _load_entry()
    ctx = _Ctx()
    entry.register(ctx)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

    def boom(*a, **k):
        raise RuntimeError("gate exploded")

    monkeypatch.setattr(gate, "evaluate_completion", boom)
    # Call exactly as invoke_hook does: cb(**kwargs). Must not raise.
    out = ctx.hooks["pre_kanban_complete"](task={"id": "t-1", "workspace_path": str(ws)})
    assert isinstance(out, dict) and out["action"] == "block"
    assert "could not be evaluated" in out["message"]
