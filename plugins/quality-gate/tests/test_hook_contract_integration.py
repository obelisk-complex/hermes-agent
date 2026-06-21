"""Integration tests that pin the REAL hook-fire contract.

Each test invokes the REGISTERED callbacks (obtained via real_load_plugin +
register()) using the FORK'S ACTUAL flat kwarg names -- the same names the
fork passes at runtime (task_id=, workspace_path=, trigger=, trigger_outcome=,
etc.) -- NOT a task= object.

Red-before / green-after evidence:
  Each test is explicitly annotated with what would happen against the
  PRE-FIX wiring (task=None everywhere) so it is clear these tests catch the
  real contract mismatch.  The annotations are in comments; the assertions
  below verify the FIXED behaviour.

Mocking strategy:
  - completion: monkeypatch gate.evaluate_completion to a controlled result.
    This avoids needing real toolchains while still exercising the full
    closure -> hook -> gate path with REAL flat kwargs.
  - blocked: monkeypatch kanban_db.connect/get_task/requeue_blocked_task to
    stubs so the DB-lookup in _bind_get_model_override is controllable.
  - spawn: monkeypatch classify.classify_tier to a fixed tier; the closure's
    ctx.llm is None so the fallback path is taken, but we verify the adapter
    correctly picks up workspace_path from flat kwargs so the sidecar is written.
"""
from __future__ import annotations

import sys
import logging
from pathlib import Path

import pytest

# Use conftest's real_load_plugin (forces a fresh exec of __init__.py under
# the real package name; does NOT re-use the stub the conftest bootstrapped
# for bare submodule aliases).
from conftest import real_load_plugin


class _Ctx:
    """Minimal hook context stub."""
    def __init__(self):
        self.hooks: dict = {}

    def register_hook(self, name: str, cb) -> None:
        self.hooks[name] = cb

    @property
    def llm(self):
        return None


def _fresh_entry_and_ctx():
    """Load a fresh plugin instance and register hooks into a stub ctx."""
    entry = real_load_plugin("hermes_plugins.quality_gate")
    ctx = _Ctx()
    entry.register(ctx)
    return entry, ctx


# ---------------------------------------------------------------------------
# Completion hook integration tests
# ---------------------------------------------------------------------------

class TestCompletionHookContract:
    """pre_kanban_complete closure must build task from FLAT kwargs."""

    def test_flat_kwargs_failing_gate_returns_block(self, tmp_path, monkeypatch):
        """
        RED-BEFORE (pre-fix): closure passed **kwargs including task=None to
        on_pre_kanban_complete -> workspace_path=None -> early-return None (allow)
        for EVERY card.  A failing gate could NEVER block.

        GREEN-AFTER (fixed): adapter extracts workspace_path from flat
        task_id=/workspace_path= kwargs -> gate IS reached -> block dict returned.
        """
        import gate as gate_mod  # conftest-aliased flat name

        entry, ctx = _fresh_entry_and_ctx()
        ws = tmp_path / "ws_fail"
        ws.mkdir()
        (ws / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")

        def fake_fail(workspace, tier, *, task_id="", **kw):
            return gate_mod.GateResult(
                passed=False,
                summary="quality-gate [standard]: FAIL\n  FAIL python/test",
            )

        monkeypatch.setattr(gate_mod, "evaluate_completion", fake_fail)

        # Invoke with the REAL flat kwargs the fork fires -- NOT task={...}.
        result = ctx.hooks["pre_kanban_complete"](
            task_id="t-99",
            result="done",
            workspace_path=str(ws),
            branch_name="feat/test",
            assignee="agent-1",
            model_override="claude-sonnet-4-5",
            blocked_attempt_count=0,
        )

        assert isinstance(result, dict), (
            "Expected a block dict; got None -- adapter still sees task=None"
        )
        assert result.get("action") == "block"
        assert "FAIL" in result.get("message", "")

    def test_flat_kwargs_passing_gate_returns_none(self, tmp_path, monkeypatch):
        """
        A workspace that passes the gate must return None (allow completion).

        RED-BEFORE: gate never reached (workspace_path was None) so None was
        returned accidentally -- gate blocks had zero effect.
        GREEN-AFTER: gate IS reached with the real workspace; passes -> None.
        """
        import gate as gate_mod

        entry, ctx = _fresh_entry_and_ctx()
        ws = tmp_path / "ws_pass"
        ws.mkdir()

        def fake_pass(workspace, tier, *, task_id="", **kw):
            return gate_mod.GateResult(
                passed=True,
                summary="quality-gate [standard]: PASS",
            )

        monkeypatch.setattr(gate_mod, "evaluate_completion", fake_pass)

        result = ctx.hooks["pre_kanban_complete"](
            task_id="t-100",
            result="done",
            workspace_path=str(ws),
            branch_name=None,
            assignee="agent-1",
            model_override=None,
            blocked_attempt_count=0,
        )
        assert result is None, f"Expected None for passing gate; got {result!r}"

    def test_missing_workspace_path_in_flat_kwargs_allows(self, tmp_path, monkeypatch):
        """
        When workspace_path is absent from the flat kwargs the hook must allow
        (return None) and log a warning -- not crash.
        """
        import gate as gate_mod

        entry, ctx = _fresh_entry_and_ctx()

        called = []
        def fake_eval(*a, **kw):
            called.append(True)
            return gate_mod.GateResult(passed=False, summary="FAIL")

        monkeypatch.setattr(gate_mod, "evaluate_completion", fake_eval)

        result = ctx.hooks["pre_kanban_complete"](
            task_id="t-no-ws",
            result="done",
            # workspace_path intentionally absent
            model_override=None,
        )
        assert result is None, "No workspace -> should allow (graceful degrade)"
        assert called == [], "evaluate_completion should not have been called"


# ---------------------------------------------------------------------------
# Blocked hook integration tests
# ---------------------------------------------------------------------------

class TestBlockedHookContract:
    """kanban_task_blocked closure must map trigger -> retriability correctly."""

    def _patch_kdb(self, monkeypatch, model_override, requeue_calls, update_calls):
        """Stub the three kanban_db seams used by the blocked adapter."""
        import hermes_cli.kanban_db as kdb

        class _FakeTask:
            pass

        fake_task = _FakeTask()
        fake_task.model_override = model_override

        class _FakeConn:
            def close(self): pass

        def fake_connect():
            return _FakeConn()

        def fake_get_task(conn, task_id):
            return fake_task

        def fake_requeue(conn, task_id, *, model_override=None, **kw):
            requeue_calls.append((task_id, model_override))
            return True

        def fake_update_field(conn, task_id, field, value):
            update_calls.append((task_id, field, value))
            return True

        monkeypatch.setattr(kdb, "connect", fake_connect, raising=False)
        monkeypatch.setattr(kdb, "get_task", fake_get_task, raising=False)
        monkeypatch.setattr(kdb, "requeue_blocked_task", fake_requeue, raising=False)
        monkeypatch.setattr(kdb, "update_task_field", fake_update_field, raising=False)

    def test_auto_block_trigger_escalates(self, monkeypatch):
        """
        RED-BEFORE: task=None -> task_id=None, model_override=None; reason was
        the raw free-text error string which is NOT in RETRIABLE_FAILURES ->
        is_retriable returned False -> NO escalation for any auto-block.

        GREEN-AFTER: trigger="auto_block" is structurally mapped to "gate_failed"
        (in RETRIABLE_FAILURES) -> is_retriable True -> requeue IS called with
        the next rung, regardless of the free-text reason content.
        """
        requeue_calls: list = []
        update_calls: list = []
        self._patch_kdb(monkeypatch, "claude-3-5-haiku", requeue_calls, update_calls)

        entry, ctx = _fresh_entry_and_ctx()

        # Fire with REAL flat kwargs from the fork's auto-block path.
        ctx.hooks["kanban_task_blocked"](
            task_id="t-auto",
            reason="Worker process crashed after 3s: OOMKilled",  # free text; NOT in RETRIABLE_FAILURES
            consecutive_failures=3,
            effective_limit=3,
            limit_source="dispatcher",
            trigger_outcome="crashed",
            trigger="auto_block",
            run_id=42,
        )

        assert len(requeue_calls) == 1, (
            f"Expected 1 requeue call for auto_block; got {requeue_calls}"
        )
        task_id, next_model = requeue_calls[0]
        assert task_id == "t-auto"
        assert next_model is not None, "Next rung must not be None (ladder must escalate)"
        assert next_model != "claude-3-5-haiku", "Must escalate beyond current rung"

    def test_manual_trigger_does_not_escalate(self, monkeypatch):
        """
        trigger="manual" must NOT escalate -- a human made this decision.

        RED-BEFORE: task=None -> reason free text -> not in RETRIABLE_FAILURES ->
        no escalation (accidentally correct result, but for the wrong reason --
        pre-fix it was also impossible to escalate on auto_block).

        GREEN-AFTER: adapter maps trigger="manual" -> non-retriable sentinel ->
        is_retriable False -> no escalation.
        """
        requeue_calls: list = []
        update_calls: list = []
        self._patch_kdb(monkeypatch, "claude-sonnet-4-5", requeue_calls, update_calls)

        entry, ctx = _fresh_entry_and_ctx()

        # Fire with REAL flat kwargs from the fork's manual-block path.
        ctx.hooks["kanban_task_blocked"](
            task_id="t-manual",
            reason="Blocked by operator: external dependency not ready",
            run_id=7,
            trigger="manual",
        )

        assert requeue_calls == [], (
            f"Manual block must NOT escalate; got requeue_calls={requeue_calls}"
        )


# ---------------------------------------------------------------------------
# Spawn hook integration tests
# ---------------------------------------------------------------------------

class TestSpawnHookContract:
    """pre_kanban_spawn closure must build task dict from FLAT kwargs."""

    def test_flat_kwargs_tier_classified_and_sidecar_written(self, tmp_path, monkeypatch):
        """
        RED-BEFORE: spawn closure passed **kwargs to on_pre_kanban_spawn without
        building a task dict -> task=None -> title="" body="" workspace_path=None
        -> tier classified on empty strings, sidecar never written.

        GREEN-AFTER: adapter builds task dict from flat kwargs ->
        title/body/workspace_path used correctly -> sidecar written.

        Note: llm is None via ctx.llm so classify_tier falls back to DEFAULT_TIER.
        We verify workspace_path is picked up by checking the sidecar is written
        (which requires workspace_path to be non-None in the task dict).
        """
        import classify as classify_mod

        # Patch classifier to verify title/body reach it correctly.
        classify_calls: list = []
        _original = classify_mod.classify_tier
        def recording_classify(title, body, *, llm, **kw):
            classify_calls.append({"title": title, "body": body})
            return "standard"  # controlled return
        monkeypatch.setattr(classify_mod, "classify_tier", recording_classify)

        entry, ctx = _fresh_entry_and_ctx()
        ws = tmp_path / "spawn_ws"
        ws.mkdir()

        # Fire with REAL flat kwargs the fork sends for pre_kanban_spawn.
        # ctx.llm is non-None requires a real LLM; here we monkeypatch
        # classify_tier directly and patch ctx.llm via a wrapper class.
        class _Ctx2(_Ctx):
            class _FakeLLM:
                def complete(self, messages, **kw):
                    class R: text = "standard"
                    return R()
            @property
            def llm(self):
                return self._FakeLLM()

        ctx2 = _Ctx2()
        entry.register(ctx2)

        result = ctx2.hooks["pre_kanban_spawn"](
            task_id="t-spawn",
            title="Refactor the authentication subsystem",
            body="Critical path change -- touch every auth endpoint",
            assignee="agent-2",
            model_override=None,
            workspace_path=str(ws),
            workspace_kind="worktree",
            branch_name="feat/auth-refactor",
            priority=10,
            skills=None,
            consecutive_failures=0,
            board="default",
        )

        # Must return a model_override dict (not None from task=None path).
        assert result is not None, (
            "Expected override dict; got None -- task dict not built from flat kwargs"
        )
        assert "model_override" in result, f"result keys: {list(result.keys())}"

        # Sidecar must have been written (proves workspace_path was non-None).
        sidecar = ws / ".hermes" / "quality-gate" / "tier"
        assert sidecar.exists(), (
            f"Tier sidecar not written -- workspace_path was None in task dict"
        )

        # classify_tier was called with the real title from flat kwargs (not "").
        assert len(classify_calls) == 1, f"classify_tier not called: {classify_calls}"
        assert "authentication" in classify_calls[0]["title"].lower(), (
            f"title not passed from flat kwargs: {classify_calls[0]['title']!r}"
        )
        # Tier from controlled classify_tier must match sidecar.
        assert "standard" in sidecar.read_text()
