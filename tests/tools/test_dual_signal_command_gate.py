"""Integration tests for the dual-signal command gate (T6-T9) and barrier (T8a/T8).

Wires the real check_all_command_guards / check_execute_code_guard with a
stubbed _smart_approve, exercising:
- legacy mode = today's behaviour (verdict alone auto-approves),
- dual_signal: tag not enabled -> manual; tag enabled + closed gate -> auto,
- never-auto-approvable tags (config.write, code.exec, security.scan) deny
  even with the tag enabled,
- head-of-line: pending record, gateway queue, and CLI prompt depth suppress
  auto-approval (control 7/9/10),
- clear_pending after a gateway resolve (control 9),
- _manual_gate_scope ignores synthetic (subagent) callbacks (control 17).
"""

import os
from unittest.mock import patch

import pytest

import tools.approval as approval_module
from tools.approval import (
    _manual_gate_scope,
    check_all_command_guards,
    check_execute_code_guard,
    clear_pending,
    resolve_gateway_approval,
    session_has_open_human_decision,
    set_current_session_key,
    submit_pending,
)


@pytest.fixture(autouse=True)
def _clean_state():
    approval_module._session_approved.clear()
    approval_module._pending.clear()
    approval_module._pending_at.clear()
    approval_module._manual_prompt_depth.clear()
    approval_module._gateway_queues.clear()
    approval_module._last_known_good_auto_approve = None
    saved = {}
    for k in ("HERMES_INTERACTIVE", "HERMES_GATEWAY_SESSION", "HERMES_EXEC_ASK",
              "HERMES_YOLO_MODE", "HERMES_CRON_SESSION"):
        if k in os.environ:
            saved[k] = os.environ.pop(k)
    yield
    approval_module._session_approved.clear()
    approval_module._pending.clear()
    approval_module._pending_at.clear()
    approval_module._manual_prompt_depth.clear()
    approval_module._gateway_queues.clear()
    for k, v in saved.items():
        os.environ[k] = v
    for k in ("HERMES_INTERACTIVE", "HERMES_GATEWAY_SESSION", "HERMES_EXEC_ASK",
              "HERMES_YOLO_MODE", "HERMES_CRON_SESSION"):
        os.environ.pop(k, None)


@pytest.fixture()
def smart_approve(monkeypatch):
    """Approve everything by default; tests override the verdict per-case."""
    monkeypatch.setattr(approval_module, "_smart_approve", lambda *_: "approve")
    yield


@pytest.fixture()
def interactive_cli(monkeypatch):
    """Fake an interactive CLI session so the guard reaches Phase 2.

    check_all_command_guards short-circuits at :4010 when not CLI/gateway/ask
    (the documented headless contract), so every Phase-2 test needs this.
    """
    monkeypatch.setattr(approval_module, "_is_interactive_cli", lambda: True)
    monkeypatch.setattr(approval_module, "_is_gateway_approval_context", lambda: False)
    yield


@pytest.fixture()
def cli_prompt(monkeypatch):
    """Stub the manual prompt; each test sets the returned choice."""
    from unittest.mock import MagicMock
    stub = MagicMock(return_value="deny")
    monkeypatch.setattr(approval_module, "prompt_dangerous_approval", stub)
    return stub


def _set_config(monkeypatch, auto_approve="dual_signal", mode="smart",
                tags=(), enabled_by=""):
    def _cfg(**kw):
        return kw

    def fake_config():
        return {
            "mode": mode,
            "auto_approve": auto_approve,
            "auto_approve_tags": list(tags),
            "auto_approve_enabled_by": enabled_by,
        }
    monkeypatch.setattr(approval_module, "_get_approval_config", fake_config)


class TestLegacyPreserved:
    def test_legacy_auto_approves_on_verdict_alone(self, smart_approve, monkeypatch,
                                                   interactive_cli):
        _set_config(monkeypatch, auto_approve="legacy")
        result = check_all_command_guards("rm -rf /tmp/build", "local")
        assert result["approved"] is True
        assert result.get("smart_approved") is True

    def test_legacy_untagged_auto_approves(self, smart_approve, monkeypatch,
                                           interactive_cli):
        _set_config(monkeypatch, auto_approve="legacy")
        # "recursive delete" tag is irrelevant under legacy.
        result = check_all_command_guards("rm -rf /tmp/build", "local")
        assert result["approved"] is True


class TestDualSignalCommandGate:
    def test_tag_not_enabled_falls_to_manual(self, smart_approve, monkeypatch,
                                             interactive_cli, cli_prompt):
        cli_prompt.return_value = "deny"
        _set_config(monkeypatch, tags=())
        result = check_all_command_guards("rm -rf /tmp/build", "local")
        assert result["approved"] is False  # tag not enabled -> manual gate
        cli_prompt.assert_called_once()

    def test_tag_enabled_auto_approves(self, smart_approve, monkeypatch,
                                       interactive_cli, cli_prompt):
        _set_config(monkeypatch, tags=("command.delete",))
        result = check_all_command_guards("rm -rf /tmp/build", "local")
        assert result["approved"] is True
        assert result.get("action_tags") == ["command.delete"]
        cli_prompt.assert_not_called()

    def test_config_write_never_auto_approves(self, smart_approve, monkeypatch,
                                              interactive_cli, cli_prompt):
        cli_prompt.return_value = "deny"
        _set_config(monkeypatch, tags=("config.write", "command.delete"))
        result = check_all_command_guards("rm -rf ~/.hermes", "local")
        assert result["approved"] is False  # G8: config.write is never-auto
        cli_prompt.assert_called_once()

    def test_guardian_deny_ignores_tags(self, monkeypatch, interactive_cli, cli_prompt):
        cli_prompt.return_value = "deny"
        _set_config(monkeypatch, tags=("command.delete",))
        monkeypatch.setattr(approval_module, "_smart_approve", lambda *_: "deny")
        # Interactive owner may override DENY for one operation — the prompt
        # must be the one-operation form (smart_denied=True) and the user's
        # deny must block.
        result = check_all_command_guards("rm -rf /tmp/build", "local")
        assert result["approved"] is False
        assert cli_prompt.call_args.kwargs.get("smart_denied") is True

    def test_guardian_approve_but_manual_gate_open(self, smart_approve, monkeypatch,
                                                   interactive_cli, cli_prompt):
        cli_prompt.return_value = "deny"
        _set_config(monkeypatch, tags=("command.delete",))
        submit_pending("default", {"command": "x", "pattern_key": "recursive delete"})
        result = check_all_command_guards("rm -rf /tmp/build", "local")
        # Head-of-line: pending record suppresses auto-approval (control 7/10).
        assert result["approved"] is False
        cli_prompt.assert_called_once()


class TestExecuteCodeGuard:
    def test_code_exec_never_auto_approves(self, smart_approve, monkeypatch):
        """Control 11: code.exec in NEVER_AUTO_APPROVABLE."""
        _set_config(monkeypatch, tags=("code.exec",))
        os.environ["HERMES_EXEC_ASK"] = "1"
        result = check_execute_code_guard("print(1)", "local")
        assert result["approved"] is False

    def test_code_exec_legacy_auto_approves(self, smart_approve, monkeypatch):
        """Legacy preserved on the execute_code surface too."""
        _set_config(monkeypatch, auto_approve="legacy")
        os.environ["HERMES_EXEC_ASK"] = "1"
        result = check_execute_code_guard("print(1)", "local")
        assert result["approved"] is True


class TestBarrierPrimitives:
    def test_session_has_open_human_decision_pending(self):
        set_current_session_key("sess-a")
        submit_pending("sess-a", {"command": "x"})
        assert session_has_open_human_decision("sess-a") is True
        clear_pending("sess-a")
        assert session_has_open_human_decision("sess-a") is False

    def test_aged_pending_is_not_open(self):
        """D17: a record older than the timeout is abandoned (control 10)."""
        set_current_session_key("sess-a")
        submit_pending("sess-a", {"command": "x"})
        # Age it beyond the (patched) timeout.
        with patch.object(approval_module, "_get_approval_timeout", return_value=0.001):
            import time
            time.sleep(0.005)
            assert session_has_open_human_decision("sess-a") is False

    def test_gateway_queue_is_open(self):
        set_current_session_key("sess-g")
        with approval_module._lock:
            approval_module._gateway_queues.setdefault("sess-g", []).append(object())
        assert session_has_open_human_decision("sess-g") is True

    def test_manual_prompt_depth_is_open(self):
        set_current_session_key("sess-c")
        with approval_module._lock:
            approval_module._manual_prompt_depth["sess-c"] = 1
        assert session_has_open_human_decision("sess-c") is True

    def test_manual_gate_scope_counts_and_clears(self):
        set_current_session_key("sess-p")
        assert session_has_open_human_decision("sess-p") is False
        with _manual_gate_scope("sess-p"):
            assert session_has_open_human_decision("sess-p") is True
        assert session_has_open_human_decision("sess-p") is False

    def test_manual_gate_scope_ignores_synthetic_callback(self):
        """Control 17: subagent callbacks are not human decisions."""
        set_current_session_key("sess-s")
        cb = lambda *a, **k: "deny"  # noqa: E731
        cb._hermes_synthetic_approval = True
        with patch("tools.terminal_tool._get_approval_callback", return_value=cb):
            with _manual_gate_scope("sess-s"):
                assert session_has_open_human_decision("sess-s") is False

    def test_resolve_gateway_clears_pending(self):
        """Control 9: /approve resolution also settles the no-notify record."""
        set_current_session_key("sess-r")
        submit_pending("sess-r", {"command": "x"})
        entry = approval_module._ApprovalEntry({"command": "x"})  # type: ignore[attr-defined]
        with approval_module._lock:
            approval_module._gateway_queues.setdefault("sess-r", []).append(entry)
        n = resolve_gateway_approval("sess-r", "once")
        assert n == 1
        assert approval_module._pending.get("sess-r") is None

    def test_clear_pending_noop_on_falsy(self):
        assert clear_pending("") is False
