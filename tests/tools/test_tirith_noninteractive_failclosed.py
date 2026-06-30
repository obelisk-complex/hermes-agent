"""Non-interactive fail-closed behaviour when tirith is UNAVAILABLE.

Covers Hermes #5 area 1: in cron and gateway sessions, when the tirith scanner
cannot actually verify a command (degraded allow, circuit breaker, or
ImportError) and the ``security.tirith_noninteractive_fail_closed`` knob is on,
the command must NOT be silently allowed. Interactive sessions, the knob-off
case, and non-degraded results stay unchanged.
"""

import logging
import sys
from unittest.mock import patch

import pytest

import tools.approval as approval_module
from tools.approval import (
    TIRITH_UNAVAIL_KEY,
    approve_session,
    check_all_command_guards,
    register_gateway_notify,
)

# A degraded "allow" result, as Step 1 now returns for the five unavailable
# branches (circuit breaker, path None, spawn failure, timeout, unexpected exit).
DEGRADED_ALLOW = {"action": "allow", "findings": [], "summary": "tirith unavailable", "degraded": True}
# A genuine (non-degraded) allow — tirith ran fine, or is disabled / unsupported.
CLEAN_ALLOW = {"action": "allow", "findings": [], "summary": ""}

CCS = "tools.tirith_security.check_command_security"


@pytest.fixture(autouse=True)
def _clean_state():
    approval_module._permanent_approved.clear()
    approval_module._session_approved.clear()
    approval_module._gateway_notify_cbs.clear()
    approval_module._gateway_queues.clear()
    approval_module._pending.clear()
    yield
    approval_module._permanent_approved.clear()
    approval_module._session_approved.clear()
    approval_module._gateway_notify_cbs.clear()
    approval_module._gateway_queues.clear()
    approval_module._pending.clear()


def _cron_env(monkeypatch):
    monkeypatch.setenv("HERMES_CRON_SESSION", "1")
    for k in ("HERMES_INTERACTIVE", "HERMES_GATEWAY_SESSION",
              "HERMES_EXEC_ASK", "HERMES_YOLO_MODE"):
        monkeypatch.delenv(k, raising=False)


# ---------------------------------------------------------------------------
# Cron sessions
# ---------------------------------------------------------------------------

class TestCronTirithFailClosed:
    """Cron has no approver. deny-mode blocks unverified commands; approve-mode
    allows them but logs loudly."""

    def test_deny_degraded_blocks(self, monkeypatch):
        _cron_env(monkeypatch)
        monkeypatch.setenv("TIRITH_NONINTERACTIVE_FAIL_CLOSED", "1")
        with patch(CCS, return_value=DEGRADED_ALLOW), \
             patch("tools.approval._get_cron_approval_mode", return_value="deny"):
            # A SAFE command (not pattern-dangerous) — proves the block is from
            # the unavailable-scanner path, not detect_dangerous_command.
            result = check_all_command_guards("echo hello", "local")
        assert result["approved"] is False
        assert "BLOCKED" in result["message"]
        assert "unavailable" in result["message"].lower()

    def test_approve_degraded_allows_with_warning(self, monkeypatch, caplog):
        _cron_env(monkeypatch)
        monkeypatch.setenv("TIRITH_NONINTERACTIVE_FAIL_CLOSED", "1")
        with patch(CCS, return_value=DEGRADED_ALLOW), \
             patch("tools.approval._get_cron_approval_mode", return_value="approve"), \
             caplog.at_level(logging.WARNING):
            result = check_all_command_guards("echo hello", "local")
        assert result["approved"] is True
        assert any(r.levelno >= logging.WARNING for r in caplog.records)

    def test_deny_importerror_blocks(self, monkeypatch):
        _cron_env(monkeypatch)
        monkeypatch.setenv("TIRITH_NONINTERACTIVE_FAIL_CLOSED", "1")
        with patch.dict(sys.modules, {"tools.tirith_security": None}), \
             patch("tools.approval._get_cron_approval_mode", return_value="deny"):
            result = check_all_command_guards("echo hello", "local")
        assert result["approved"] is False
        assert "BLOCKED" in result["message"]

    def test_approve_importerror_allows_with_warning(self, monkeypatch, caplog):
        _cron_env(monkeypatch)
        monkeypatch.setenv("TIRITH_NONINTERACTIVE_FAIL_CLOSED", "1")
        with patch.dict(sys.modules, {"tools.tirith_security": None}), \
             patch("tools.approval._get_cron_approval_mode", return_value="approve"), \
             caplog.at_level(logging.WARNING):
            result = check_all_command_guards("echo hello", "local")
        assert result["approved"] is True
        assert any(r.levelno >= logging.WARNING for r in caplog.records)

    def test_knob_on_via_config_only_blocks(self, monkeypatch):
        """R4 guard: with NO env var, the knob must read from config via
        cfg_get(..., default=True). If the cfg_get call drops the default=
        keyword, the knob silently reads False and this command is allowed —
        so this test fails loudly on that regression."""
        _cron_env(monkeypatch)
        monkeypatch.delenv("TIRITH_NONINTERACTIVE_FAIL_CLOSED", raising=False)
        cfg = {"security": {"tirith_noninteractive_fail_closed": True},
               "approvals": {"cron_mode": "deny"}}
        with patch("hermes_cli.config.load_config", return_value=cfg), \
             patch(CCS, return_value=DEGRADED_ALLOW), \
             patch("tools.approval._get_cron_approval_mode", return_value="deny"):
            result = check_all_command_guards("echo hello", "local")
        assert result["approved"] is False
        assert "unavailable" in result["message"].lower()

    def test_knob_off_restores_failopen(self, monkeypatch):
        _cron_env(monkeypatch)
        monkeypatch.setenv("TIRITH_NONINTERACTIVE_FAIL_CLOSED", "0")
        with patch(CCS, return_value=DEGRADED_ALLOW), \
             patch("tools.approval._get_cron_approval_mode", return_value="deny"):
            result = check_all_command_guards("echo hello", "local")
        assert result["approved"] is True  # prior fail-open behaviour

    def test_non_degraded_is_inert(self, monkeypatch):
        """A clean (non-degraded) allow — tirith ran fine, or is disabled, or
        the platform is unsupported — must not be blocked even with the knob on."""
        _cron_env(monkeypatch)
        monkeypatch.setenv("TIRITH_NONINTERACTIVE_FAIL_CLOSED", "1")
        with patch(CCS, return_value=CLEAN_ALLOW), \
             patch("tools.approval._get_cron_approval_mode", return_value="deny"):
            result = check_all_command_guards("echo hello", "local")
        assert result["approved"] is True


# ---------------------------------------------------------------------------
# Gateway sessions (a human approver exists, reached via the async flow)
# ---------------------------------------------------------------------------

class TestGatewayTirithFailClosed:
    SESSION_KEY = "test-tirith-unavail-gw"

    def setup_method(self):
        import os
        mod = approval_module
        mod._gateway_queues.clear()
        mod._gateway_notify_cbs.clear()
        mod._session_approved.clear()
        mod._permanent_approved.clear()
        mod._pending.clear()
        self._saved_env = {
            k: os.environ.get(k)
            for k in ("HERMES_GATEWAY_SESSION", "HERMES_CRON_SESSION",
                      "HERMES_YOLO_MODE", "HERMES_SESSION_KEY",
                      "HERMES_INTERACTIVE", "HERMES_EXEC_ASK",
                      "TIRITH_NONINTERACTIVE_FAIL_CLOSED")
        }
        os.environ.pop("HERMES_YOLO_MODE", None)
        os.environ.pop("HERMES_INTERACTIVE", None)
        os.environ.pop("HERMES_CRON_SESSION", None)
        os.environ.pop("HERMES_EXEC_ASK", None)
        os.environ["HERMES_GATEWAY_SESSION"] = "1"
        os.environ["HERMES_SESSION_KEY"] = self.SESSION_KEY
        os.environ["TIRITH_NONINTERACTIVE_FAIL_CLOSED"] = "1"

    def teardown_method(self):
        import os
        mod = approval_module
        mod._gateway_queues.clear()
        mod._gateway_notify_cbs.clear()
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _force_short_timeout(self, monkeypatch, seconds=1):
        monkeypatch.setattr(
            approval_module, "_get_approval_config",
            lambda: {"mode": "manual", "gateway_timeout": seconds, "timeout": seconds},
        )

    def test_degraded_surfaces_not_silently_allowed(self, monkeypatch):
        self._force_short_timeout(monkeypatch, 1)
        # Register under the key the guard will actually compute on THIS thread
        # (get_current_session_key prefers a contextvar a prior test may leak).
        key = approval_module.get_current_session_key()
        notified = []
        register_gateway_notify(key, lambda d: notified.append(d))
        with patch(CCS, return_value=DEGRADED_ALLOW):
            result = check_all_command_guards("echo hello", "local")
        assert result["approved"] is False          # not silently allowed
        assert len(notified) == 1                    # the user WAS asked

    def test_importerror_surfaces_not_silently_allowed(self, monkeypatch):
        self._force_short_timeout(monkeypatch, 1)
        key = approval_module.get_current_session_key()
        notified = []
        register_gateway_notify(key, lambda d: notified.append(d))
        with patch.dict(sys.modules, {"tools.tirith_security": None}):
            result = check_all_command_guards("echo hello", "local")
        assert result["approved"] is False
        assert len(notified) == 1

    def test_smart_mode_does_not_auto_approve(self, monkeypatch):
        """Step 3e: the scanner-unavailable warning must skip _smart_approve so
        the aux LLM can never auto-approve an unverifiable command."""
        self._force_short_timeout(monkeypatch, 1)
        key = approval_module.get_current_session_key()
        register_gateway_notify(key, lambda d: None)
        with patch(CCS, return_value=DEGRADED_ALLOW), \
             patch("tools.approval._get_approval_mode", return_value="smart"), \
             patch("tools.approval._smart_approve", return_value="approve") as mock_smart:
            result = check_all_command_guards("echo hello", "local")
        assert result["approved"] is False
        assert not result.get("smart_approved")
        mock_smart.assert_not_called()

    def test_prior_session_approval_still_reprompts(self, monkeypatch):
        """Step 3f READ guard: a previous session approval of the unavailable
        key must NOT short-circuit — every unverifiable command re-prompts."""
        self._force_short_timeout(monkeypatch, 1)
        key = approval_module.get_current_session_key()
        notified = []
        register_gateway_notify(key, lambda d: notified.append(d))
        approve_session(key, TIRITH_UNAVAIL_KEY)
        with patch(CCS, return_value=DEGRADED_ALLOW):
            result = check_all_command_guards("echo hello", "local")
        assert result["approved"] is False
        assert len(notified) == 1

    def test_session_choice_does_not_persist_unavailable_key(self, monkeypatch):
        """Step 3f WRITE guard: approving the unavailable warning at session
        scope must not memoise it (the next command must re-prompt)."""
        from tools import approval as mod
        import threading
        import time

        def _run_once():
            with patch(CCS, return_value=DEGRADED_ALLOW):
                check_all_command_guards("echo hello", "local")

        register_gateway_notify(self.SESSION_KEY, lambda d: None)
        t = threading.Thread(target=_run_once)
        t.start()
        for _ in range(100):
            if mod._gateway_queues.get(self.SESSION_KEY):
                break
            time.sleep(0.02)
        mod.resolve_gateway_approval(self.SESSION_KEY, "session")
        t.join(timeout=5)
        # The unavailable key must NOT have been recorded as session-approved.
        assert not mod.is_approved(self.SESSION_KEY, TIRITH_UNAVAIL_KEY)


# ---------------------------------------------------------------------------
# Interactive path is unchanged (the existing tirith-import-error contract)
# ---------------------------------------------------------------------------

def test_interactive_importerror_failclosed_uses_existing_path(monkeypatch):
    """is_cli + ImportError + tirith_fail_open=false routes through the EXISTING
    'tirith-import-error' warning, not the new non-interactive synth."""
    monkeypatch.setenv("HERMES_INTERACTIVE", "1")
    for k in ("HERMES_CRON_SESSION", "HERMES_GATEWAY_SESSION",
              "HERMES_EXEC_ASK", "HERMES_YOLO_MODE",
              "TIRITH_NONINTERACTIVE_FAIL_CLOSED"):
        monkeypatch.delenv(k, raising=False)

    cfg = {"security": {"tirith_enabled": True, "tirith_fail_open": False},
           "approvals": {"mode": "manual"}}
    captured = {}

    def fake_prompt(cmd, desc, **kwargs):
        captured["desc"] = desc
        return "once"  # allow this single time after surfacing

    with patch.dict(sys.modules, {"tools.tirith_security": None}), \
         patch("hermes_cli.config.load_config", return_value=cfg), \
         patch("tools.approval.prompt_dangerous_approval", side_effect=fake_prompt):
        result = check_all_command_guards("echo hello", "local")

    assert result["approved"] is True                    # allowed once, after prompt
    assert "import" in captured["desc"].lower()           # existing import-error wording
    assert "non-interactive session" not in captured["desc"].lower()  # NOT the new synth
