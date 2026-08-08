"""Phase B (T11/T12): action tags on the surfaces that have no author verdict.

Spec: /media/owner/Workspace/hermes-fixes-plans/2026-08-07-dual-signal-approval-gate-plan.md
(T11, T12, D4, spec line 390). These four surfaces never call
evaluate_dual_signal: they carry tags for observability only, and every test
here is paired with a parity control proving the approve/deny/prompt outcome
is byte-identical to today's under dual_signal with every configurable tag
enabled.
"""

import os

import pytest

import tools.approval as approval_module
from tools.action_tags import CONFIGURABLE_TAGS
from tools.approval import (
    check_execute_code_guard,
    request_elicitation_consent,
    request_tool_approval,
)

ALL_CONFIGURABLE = tuple(sorted(CONFIGURABLE_TAGS))


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    approval_module._session_approved.clear()
    approval_module._pending.clear()
    approval_module._pending_at.clear()
    approval_module._manual_prompt_depth.clear()
    approval_module._gateway_queues.clear()
    approval_module._last_known_good_auto_approve = None
    monkeypatch.setattr(
        approval_module, "get_current_session_key",
        lambda default="default": "phase-b-session",
    )
    monkeypatch.setattr(approval_module, "is_approved", lambda sk, pk: False)
    monkeypatch.setattr(approval_module, "is_current_session_yolo_enabled", lambda: False)
    monkeypatch.setattr(approval_module, "_YOLO_MODE_FROZEN", False, raising=False)
    monkeypatch.setattr(
        "tools.terminal_tool._get_approval_callback", lambda: None, raising=False
    )
    saved = {}
    for key in ("HERMES_INTERACTIVE", "HERMES_GATEWAY_SESSION", "HERMES_EXEC_ASK",
                "HERMES_YOLO_MODE", "HERMES_CRON_SESSION"):
        if key in os.environ:
            saved[key] = os.environ.pop(key)
    yield
    for key, value in saved.items():
        os.environ[key] = value


def _set_config(monkeypatch, auto_approve="dual_signal", mode="smart",
                tags=ALL_CONFIGURABLE, enabled_by=""):
    """Point the approval layer at an in-memory approvals config block."""
    def fake_config():
        return {
            "mode": mode,
            "auto_approve": auto_approve,
            "auto_approve_tags": list(tags),
            "auto_approve_enabled_by": enabled_by,
        }
    monkeypatch.setattr(approval_module, "_get_approval_config", fake_config)


class TestApprovalGateParity:
    """Control: dual_signal + every configurable tag changes no outcome here."""

    @pytest.mark.parametrize("choice,expected_approved", [
        ("once", True),
        ("session", True),
        ("deny", False),
        ("timeout", False),
    ])
    def test_cli_outcome_unchanged_under_dual_signal(self, monkeypatch, choice,
                                                     expected_approved):
        _set_config(monkeypatch)
        monkeypatch.setattr(approval_module, "_is_interactive_cli", lambda: True)
        monkeypatch.setattr(approval_module, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(approval_module, "prompt_dangerous_approval",
                            lambda *a, **k: choice)
        monkeypatch.setattr(approval_module, "approve_session", lambda sk, pk: None)
        res = request_tool_approval("write_file", "writing ~/.ssh/authorized_keys")
        assert res["approved"] is expected_approved

    def test_cron_deny_outcome_unchanged_under_dual_signal(self, monkeypatch):
        _set_config(monkeypatch)
        monkeypatch.setattr(approval_module, "_is_interactive_cli", lambda: False)
        monkeypatch.setattr(approval_module, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(approval_module, "_is_cron_approval_context", lambda: True)
        monkeypatch.setattr(approval_module, "_get_cron_approval_mode", lambda: "deny")
        res = request_tool_approval("terminal", "smtp send")
        assert res["approved"] is False
        assert "cron" in res["message"].lower()

    def test_no_human_still_fails_closed_under_dual_signal(self, monkeypatch):
        _set_config(monkeypatch)
        monkeypatch.setattr(approval_module, "_is_interactive_cli", lambda: False)
        monkeypatch.setattr(approval_module, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(approval_module, "_is_cron_approval_context", lambda: False)
        res = request_tool_approval("terminal", "smtp send")
        assert res["approved"] is False
        assert "no interactive user or gateway" in res["message"].lower()


class TestApprovalGateNoAutoApproval:
    """D4 / spec line 390: this surface must never reach evaluate_dual_signal."""

    def test_dual_signal_gate_is_never_called(self, monkeypatch):
        import tools.auto_approval as auto_approval_module

        def _boom(**kwargs):
            raise AssertionError(
                "evaluate_dual_signal reached _run_approval_gate - "
                "D4 forbids an auto-approval path on this surface"
            )
        monkeypatch.setattr(auto_approval_module, "evaluate_dual_signal", _boom)
        _set_config(monkeypatch)
        monkeypatch.setattr(approval_module, "_is_interactive_cli", lambda: True)
        monkeypatch.setattr(approval_module, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(approval_module, "prompt_dangerous_approval",
                            lambda *a, **k: "deny")
        res = request_tool_approval("terminal", "curl PUT to external API")
        assert res["approved"] is False


class TestApprovalGateTags:
    def test_plugin_rule_result_carries_the_tag(self, monkeypatch):
        _set_config(monkeypatch)
        monkeypatch.setattr(approval_module, "_is_interactive_cli", lambda: True)
        monkeypatch.setattr(approval_module, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(approval_module, "prompt_dangerous_approval",
                            lambda *a, **k: "deny")
        res = request_tool_approval("write_file", "writing ~/.ssh/authorized_keys")
        assert res["action_tags"] == ["plugin.rule"]

    def test_approved_result_carries_the_tag(self, monkeypatch):
        _set_config(monkeypatch)
        monkeypatch.setattr(approval_module, "_is_interactive_cli", lambda: True)
        monkeypatch.setattr(approval_module, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(approval_module, "prompt_dangerous_approval",
                            lambda *a, **k: "once")
        res = request_tool_approval("write_file", "writing ~/.ssh/authorized_keys")
        assert res["approved"] is True
        assert res["action_tags"] == ["plugin.rule"]

    def test_cron_deny_result_carries_the_tag(self, monkeypatch):
        _set_config(monkeypatch)
        monkeypatch.setattr(approval_module, "_is_interactive_cli", lambda: False)
        monkeypatch.setattr(approval_module, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(approval_module, "_is_cron_approval_context", lambda: True)
        monkeypatch.setattr(approval_module, "_get_cron_approval_mode", lambda: "deny")
        res = request_tool_approval("terminal", "smtp send")
        assert res["action_tags"] == ["plugin.rule"]

    def test_audit_line_names_the_tag(self, monkeypatch, caplog):
        _set_config(monkeypatch)
        monkeypatch.setattr(approval_module, "_is_interactive_cli", lambda: True)
        monkeypatch.setattr(approval_module, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(approval_module, "prompt_dangerous_approval",
                            lambda *a, **k: "deny")
        with caplog.at_level("INFO", logger="tools.approval"):
            request_tool_approval("terminal", "curl PUT", rule_key="ext")
        lines = [r.getMessage() for r in caplog.records
                 if "action-tags surface=approval_gate" in r.getMessage()]
        assert len(lines) == 1
        assert "tags=plugin.rule" in lines[0]
        assert "auto_approvable=no" in lines[0]

    def test_yolo_short_circuit_shape_is_untouched(self, monkeypatch):
        """The two pre-gate short-circuits keep their historical dict exactly."""
        _set_config(monkeypatch)
        monkeypatch.setattr(approval_module, "is_current_session_yolo_enabled",
                            lambda: True)
        res = request_tool_approval("terminal", "curl PUT", rule_key="ext")
        assert res == {"approved": True, "message": None}

    def test_session_allowlist_short_circuit_shape_is_untouched(self, monkeypatch):
        _set_config(monkeypatch)
        monkeypatch.setattr(approval_module, "is_approved", lambda sk, pk: True)
        res = request_tool_approval("write_file", "sensitive path", rule_key="ssh")
        assert res == {"approved": True, "message": None}


class TestElicitationParity:
    """Control: dual_signal + every configurable tag changes no outcome here."""

    @pytest.mark.parametrize("choice,expected", [
        ("once", "accept"),
        ("session", "accept"),
        ("always", "accept"),
        ("deny", "decline"),
        ("timeout", "cancel"),
    ])
    def test_cli_outcome_unchanged_under_dual_signal(self, monkeypatch, choice, expected):
        _set_config(monkeypatch)
        monkeypatch.setattr(approval_module, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(approval_module, "prompt_dangerous_approval",
                            lambda *a, **k: choice)
        assert request_elicitation_consent("confirm?", "server asks") == expected

    def test_prompt_exception_still_declines_under_dual_signal(self, monkeypatch):
        _set_config(monkeypatch)
        monkeypatch.setattr(approval_module, "_is_gateway_approval_context", lambda: False)

        def _raise(*a, **k):
            raise RuntimeError("prompt exploded")
        monkeypatch.setattr(approval_module, "prompt_dangerous_approval", _raise)
        assert request_elicitation_consent("confirm?", "server asks") == "decline"


class TestElicitationNoAutoApproval:
    def test_dual_signal_gate_is_never_called(self, monkeypatch):
        import tools.auto_approval as auto_approval_module

        def _boom(**kwargs):
            raise AssertionError(
                "evaluate_dual_signal reached the elicitation gate - "
                "D4 forbids an auto-approval path on this surface"
            )
        monkeypatch.setattr(auto_approval_module, "evaluate_dual_signal", _boom)
        _set_config(monkeypatch)
        monkeypatch.setattr(approval_module, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(approval_module, "prompt_dangerous_approval",
                            lambda *a, **k: "deny")
        assert request_elicitation_consent("confirm?", "server asks") == "decline"


class TestElicitationTags:
    def test_audit_line_names_the_mcp_tool_tag(self, monkeypatch, caplog):
        _set_config(monkeypatch)
        monkeypatch.setattr(approval_module, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(approval_module, "prompt_dangerous_approval",
                            lambda *a, **k: "once")
        with caplog.at_level("INFO", logger="tools.approval"):
            assert request_elicitation_consent("confirm?", "server asks") == "accept"
        lines = [r.getMessage() for r in caplog.records
                 if "action-tags surface=mcp_elicitation" in r.getMessage()]
        assert len(lines) == 1
        assert "tags=mcp.tool" in lines[0]
        assert "pattern_key=mcp_elicitation" in lines[0]

    def test_mcp_tool_tag_is_not_configurable(self):
        from tools.action_tags import NOT_WIRED
        assert "mcp.tool" in NOT_WIRED
        assert "mcp.tool" not in CONFIGURABLE_TAGS


class TestExecuteCodeParity:
    """Control: dual_signal + every configurable tag changes no outcome here.

    code.exec is in NEVER_AUTO_APPROVABLE (D9), so no configuration can move
    any of these branches.
    """

    @pytest.fixture(autouse=True)
    def _manual_mode(self, monkeypatch):
        """Pin approvals.mode.

        check_execute_code_guard reads the mode separately from the approvals
        block (:4677), so an unpinned mode lets the machine's real config.yaml
        move a branch: `off` returns above the gate, `smart` calls the real
        guardian LLM. Every branch under test here returns before the smart
        branch, so `manual` isolates the auto_approve axis.
        """
        monkeypatch.setattr(approval_module, "_get_approval_mode", lambda: "manual")

    def test_container_backend_still_skips(self, monkeypatch):
        """Untagged distinguishes the container-skip bypass from the local-CLI
        allow branch (:4770), which also returns approved=True under this
        fixture but goes through the gate and is tagged.
        """
        _set_config(monkeypatch)
        res_vercel = check_execute_code_guard("print(1)", "vercel_sandbox")
        assert res_vercel["approved"] is True
        assert "action_tags" not in res_vercel
        res_singularity = check_execute_code_guard("print(1)", "singularity")
        assert res_singularity["approved"] is True
        assert "action_tags" not in res_singularity

    def test_yolo_still_bypasses(self, monkeypatch):
        _set_config(monkeypatch)
        monkeypatch.setattr(approval_module, "is_current_session_yolo_enabled",
                            lambda: True)
        assert check_execute_code_guard("print(1)", "local")["approved"] is True

    def test_cron_deny_still_blocks(self, monkeypatch):
        _set_config(monkeypatch)
        monkeypatch.setattr(approval_module, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(approval_module, "_is_cron_approval_context", lambda: True)
        monkeypatch.setattr(approval_module, "_get_cron_approval_mode", lambda: "deny")
        res = check_execute_code_guard("print(1)", "local")
        assert res["approved"] is False
        assert res["outcome"] == "blocked"

    def test_cron_approve_still_allows(self, monkeypatch):
        _set_config(monkeypatch)
        monkeypatch.setattr(approval_module, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(approval_module, "_is_cron_approval_context", lambda: True)
        monkeypatch.setattr(approval_module, "_get_cron_approval_mode", lambda: "approve")
        assert check_execute_code_guard("print(1)", "local")["approved"] is True

    def test_local_non_gateway_still_allows(self, monkeypatch):
        """R18: the documented pre-existing whole-script gap, asserted not hidden."""
        _set_config(monkeypatch)
        monkeypatch.setattr(approval_module, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(approval_module, "_is_cron_approval_context", lambda: False)
        assert check_execute_code_guard("print(1)", "local")["approved"] is True

    def test_session_allowlist_hit_still_allows(self, monkeypatch):
        _set_config(monkeypatch)
        monkeypatch.setattr(approval_module, "_is_gateway_approval_context", lambda: True)
        monkeypatch.setattr(approval_module, "_is_cron_approval_context", lambda: False)
        monkeypatch.setattr(approval_module, "is_approved", lambda sk, pk: True)
        assert check_execute_code_guard("print(1)", "local")["approved"] is True

    def test_gateway_without_notifier_still_pends(self, monkeypatch):
        _set_config(monkeypatch)
        monkeypatch.setattr(approval_module, "_is_gateway_approval_context", lambda: True)
        monkeypatch.setattr(approval_module, "_is_cron_approval_context", lambda: False)
        res = check_execute_code_guard("print(1)", "local")
        assert res["approved"] is False
        assert res["status"] == "pending_approval"


class TestExecuteCodeTags:
    @pytest.fixture(autouse=True)
    def _manual_mode(self, monkeypatch):
        """Same pin as TestExecuteCodeParity: no unpinned mode, no live LLM."""
        monkeypatch.setattr(approval_module, "_get_approval_mode", lambda: "manual")

    def test_cron_deny_carries_code_exec(self, monkeypatch):
        _set_config(monkeypatch)
        monkeypatch.setattr(approval_module, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(approval_module, "_is_cron_approval_context", lambda: True)
        monkeypatch.setattr(approval_module, "_get_cron_approval_mode", lambda: "deny")
        res = check_execute_code_guard("print(1)", "local")
        assert res["action_tags"] == ["code.exec"]

    def test_cron_approve_carries_code_exec(self, monkeypatch):
        _set_config(monkeypatch)
        monkeypatch.setattr(approval_module, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(approval_module, "_is_cron_approval_context", lambda: True)
        monkeypatch.setattr(approval_module, "_get_cron_approval_mode", lambda: "approve")
        res = check_execute_code_guard("print(1)", "local")
        assert res["action_tags"] == ["code.exec"]

    def test_local_non_gateway_allow_carries_code_exec(self, monkeypatch):
        _set_config(monkeypatch)
        monkeypatch.setattr(approval_module, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(approval_module, "_is_cron_approval_context", lambda: False)
        res = check_execute_code_guard("print(1)", "local")
        assert res["action_tags"] == ["code.exec"]

    def test_session_allowlist_hit_carries_code_exec(self, monkeypatch):
        _set_config(monkeypatch)
        monkeypatch.setattr(approval_module, "_is_gateway_approval_context", lambda: True)
        monkeypatch.setattr(approval_module, "_is_cron_approval_context", lambda: False)
        monkeypatch.setattr(approval_module, "is_approved", lambda sk, pk: True)
        res = check_execute_code_guard("print(1)", "local")
        assert res["action_tags"] == ["code.exec"]

    def test_pending_result_carries_code_exec(self, monkeypatch):
        _set_config(monkeypatch)
        monkeypatch.setattr(approval_module, "_is_gateway_approval_context", lambda: True)
        monkeypatch.setattr(approval_module, "_is_cron_approval_context", lambda: False)
        res = check_execute_code_guard("print(1)", "local")
        assert res["action_tags"] == ["code.exec"]

    def test_audit_line_names_code_exec(self, monkeypatch, caplog):
        _set_config(monkeypatch)
        monkeypatch.setattr(approval_module, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(approval_module, "_is_cron_approval_context", lambda: False)
        with caplog.at_level("INFO", logger="tools.approval"):
            check_execute_code_guard("print(1)", "local")
        lines = [r.getMessage() for r in caplog.records
                 if "action-tags surface=execute_code_guard" in r.getMessage()]
        assert len(lines) == 1
        assert "tags=code.exec" in lines[0]

    def test_bypass_returns_stay_untagged(self, monkeypatch):
        """All three pre-gate bypasses return before the gate is entered.

        vercel_sandbox and _should_skip_container_guards (singularity here)
        are distinct return statements (:4719-4722); yolo is a third.
        """
        _set_config(monkeypatch)
        assert "action_tags" not in check_execute_code_guard("print(1)", "vercel_sandbox")
        assert "action_tags" not in check_execute_code_guard("print(1)", "singularity")
        monkeypatch.setattr(approval_module, "is_current_session_yolo_enabled",
                            lambda: True)
        assert "action_tags" not in check_execute_code_guard("print(1)", "local")
