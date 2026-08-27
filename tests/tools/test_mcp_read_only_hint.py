"""T12: MCP readOnlyHint captured at discovery, as an observation only.

The hint is self-declaration by an untrusted server. It may only ever add
friction: it is never an input to evaluate_dual_signal, and mcp.tool is in
NOT_WIRED, so no listing can make anything auto-approve.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("mcp.types")

from tools.mcp_tool import (  # noqa: E402
    MCPServerTask,
    _extract_read_only_hint,
    _register_server_tools,
    mcp_tool_read_only_hint,
)
from tools.registry import ToolRegistry  # noqa: E402


def _tool(name, annotations=None):
    return SimpleNamespace(
        name=name, description="d", inputSchema=None, annotations=annotations
    )


class TestExtractReadOnlyHint:
    def test_object_annotations(self):
        assert _extract_read_only_hint(
            _tool("t", SimpleNamespace(readOnlyHint=True))
        ) is True
        assert _extract_read_only_hint(
            _tool("t", SimpleNamespace(readOnlyHint=False))
        ) is False

    def test_dict_annotations(self):
        assert _extract_read_only_hint(_tool("t", {"readOnlyHint": True})) is True

    def test_absent_or_unusable_is_none(self):
        assert _extract_read_only_hint(_tool("t", None)) is None
        assert _extract_read_only_hint(_tool("t", SimpleNamespace())) is None
        assert _extract_read_only_hint(_tool("t", {"readOnlyHint": "yes"})) is None
        # A listing from the schema cache has no annotations attribute at all.
        assert _extract_read_only_hint(
            SimpleNamespace(name="t", description="d", inputSchema=None)
        ) is None


class TestCaptureThroughRegistration:
    def test_hints_are_recorded_per_server_and_tool(self):
        server = MCPServerTask("hint_srv")
        server._tools = [
            _tool("reader", SimpleNamespace(readOnlyHint=True)),
            _tool("writer", SimpleNamespace(readOnlyHint=False)),
            _tool("silent", None),
        ]
        server.session = MagicMock()
        with patch("tools.registry.registry", ToolRegistry()):
            _register_server_tools("hint_srv", server, {})
        assert mcp_tool_read_only_hint("hint_srv", "reader") is True
        assert mcp_tool_read_only_hint("hint_srv", "writer") is False
        assert mcp_tool_read_only_hint("hint_srv", "silent") is None
        assert mcp_tool_read_only_hint("hint_srv", "absent") is None
        assert mcp_tool_read_only_hint("other_srv", "reader") is None

    def test_refresh_replaces_the_previous_capture(self):
        server = MCPServerTask("hint_srv2")
        server.session = MagicMock()
        server._tools = [_tool("reader", SimpleNamespace(readOnlyHint=True))]
        with patch("tools.registry.registry", ToolRegistry()):
            _register_server_tools("hint_srv2", server, {})
        assert mcp_tool_read_only_hint("hint_srv2", "reader") is True
        # The server re-lists the same tool, now declaring it writable.
        server._tools = [_tool("reader", SimpleNamespace(readOnlyHint=False))]
        with patch("tools.registry.registry", ToolRegistry()):
            _register_server_tools("hint_srv2", server, {})
        assert mcp_tool_read_only_hint("hint_srv2", "reader") is False


import asyncio  # noqa: E402  -- top-level imports above are importorskip-gated

from mcp.types import ElicitResult  # noqa: E402
from tools.mcp_tool import ElicitationHandler  # noqa: E402


def _form_params(message="please confirm"):
    return SimpleNamespace(mode="form", message=message, requested_schema={})


class TestReadOnlyHintCannotAutoApprove:
    """T12's verify line: a fake listing declaring readOnlyHint: true cannot
    reach auto_approved=True, including through the elicitation gate."""

    def test_hint_true_does_not_change_the_elicitation_outcome(self, monkeypatch):
        import tools.approval as approval_module
        from tools.action_tags import CONFIGURABLE_TAGS

        server = MCPServerTask("ro_srv")
        server.session = MagicMock()
        server._tools = [_tool("reader", SimpleNamespace(readOnlyHint=True))]
        with patch("tools.registry.registry", ToolRegistry()):
            _register_server_tools("ro_srv", server, {})

        def fake_config():
            return {
                "mode": "smart",
                "auto_approve": "dual_signal",
                "auto_approve_tags": sorted(CONFIGURABLE_TAGS),
                "auto_approve_enabled_by": "",
            }
        monkeypatch.setattr(approval_module, "_get_approval_config", fake_config)
        monkeypatch.setattr(approval_module, "_is_gateway_approval_context",
                            lambda: False)
        monkeypatch.setattr(approval_module, "prompt_dangerous_approval",
                            lambda *a, **k: "deny")

        # A readOnlyHint of True must not turn a denial into an approval.
        assert approval_module.request_elicitation_consent(
            "confirm?", "server asks", read_only_hint=True,
        ) == "decline"

    def test_elicitation_never_calls_the_dual_signal_gate(self, monkeypatch):
        import tools.approval as approval_module
        import tools.auto_approval as auto_approval_module

        def _boom(**kwargs):
            raise AssertionError(
                "evaluate_dual_signal reached the elicitation gate - "
                "readOnlyHint is advisory and mcp.tool is in NOT_WIRED"
            )
        monkeypatch.setattr(auto_approval_module, "evaluate_dual_signal", _boom)
        monkeypatch.setattr(approval_module, "_is_gateway_approval_context",
                            lambda: False)
        monkeypatch.setattr(approval_module, "prompt_dangerous_approval",
                            lambda *a, **k: "deny")
        assert approval_module.request_elicitation_consent(
            "confirm?", "server asks", read_only_hint=True,
        ) == "decline"

    def test_handler_outcome_is_unchanged_by_the_hint(self):
        """The handler's accept/decline/cancel mapping does not move."""
        handler = ElicitationHandler("ro_srv", {"timeout": 5})
        with patch("tools.approval.request_elicitation_consent",
                   return_value="accept"):
            result = asyncio.run(handler(context=None, params=_form_params()))
        assert isinstance(result, ElicitResult)
        assert result.action == "accept"


class TestHintReachesTheElicitationGate:
    def test_in_flight_tool_hint_is_passed_through(self):
        server = MCPServerTask("e2e_srv")
        server.session = MagicMock()
        server._tools = [_tool("reader", SimpleNamespace(readOnlyHint=True))]
        with patch("tools.registry.registry", ToolRegistry()):
            _register_server_tools("e2e_srv", server, {})
        server._pending_call_tool_name = "reader"

        handler = ElicitationHandler("e2e_srv", {"timeout": 5}, owner=server)
        seen = {}

        def _record(message, description, **kwargs):
            seen.update(kwargs)
            return "decline"

        with patch("tools.approval.request_elicitation_consent", _record):
            result = asyncio.run(handler(context=None, params=_form_params()))

        assert result.action == "decline"
        assert seen["read_only_hint"] is True

    def test_no_in_flight_tool_passes_none(self):
        server = MCPServerTask("e2e_srv2")
        server.session = MagicMock()
        server._tools = [_tool("reader", SimpleNamespace(readOnlyHint=True))]
        with patch("tools.registry.registry", ToolRegistry()):
            _register_server_tools("e2e_srv2", server, {})
        # No call in flight: _pending_call_tool_name is None from __init__.
        handler = ElicitationHandler("e2e_srv2", {"timeout": 5}, owner=server)
        seen = {}

        def _record(message, description, **kwargs):
            seen.update(kwargs)
            return "decline"

        with patch("tools.approval.request_elicitation_consent", _record):
            asyncio.run(handler(context=None, params=_form_params()))
        assert seen["read_only_hint"] is None

    def test_audit_line_records_the_hint(self, monkeypatch, caplog):
        import tools.approval as approval_module
        monkeypatch.setattr(approval_module, "_is_gateway_approval_context",
                            lambda: False)
        monkeypatch.setattr(approval_module, "prompt_dangerous_approval",
                            lambda *a, **k: "once")
        with caplog.at_level("INFO", logger="tools.approval"):
            approval_module.request_elicitation_consent(
                "confirm?", "server asks", read_only_hint=True,
            )
        lines = [r.getMessage() for r in caplog.records
                 if "action-tags surface=mcp_elicitation" in r.getMessage()]
        assert len(lines) == 1
        assert "tags=mcp.tool" in lines[0]
        assert "read_only_hint=True" in lines[0]


def _patch_mcp_loop_run_directly():
    """Match TestToolHandler._patch_mcp_loop in test_mcp_tool.py: run the
    coroutine handed to _run_on_mcp_loop synchronously via asyncio.run, so a
    handler built by _make_tool_handler actually executes its _call()
    closure instead of being scheduled on a background loop."""
    def fake_run(coro_or_factory, timeout=30):
        coro = coro_or_factory() if callable(coro_or_factory) else coro_or_factory
        return asyncio.run(coro)
    return patch("tools.mcp_tool._run_on_mcp_loop", side_effect=fake_run)


class TestPendingCallToolNameLifecycle:
    """Fix-round Finding 1: the set/clear of _pending_call_tool_name inside
    _make_tool_handler._call had zero coverage — every existing test set the
    attribute by hand rather than driving a real call. Deleting either the
    set line or the finally-clear line left all tests green. This test
    drives a real session.call_tool through the real _call() closure and
    observes the attribute from inside the mocked call itself, so it fails
    if either line goes missing."""

    def test_tool_name_is_set_during_the_call_and_cleared_after(self):
        from tools.mcp_tool import _make_tool_handler, _servers

        server = MCPServerTask("call_lifecycle_srv")
        seen_during_call = {}

        async def _capture(name, arguments=None):
            # Runs "inside" session.call_tool -- i.e. strictly after the
            # real `server._pending_call_tool_name = tool_name` line and
            # strictly before its `finally` clears it. If that set line is
            # deleted, this observes None instead of "reader".
            seen_during_call["tool_name"] = server._pending_call_tool_name
            return SimpleNamespace(
                content=[SimpleNamespace(text="ok")], isError=False,
            )

        mock_session = MagicMock()
        mock_session.call_tool = AsyncMock(side_effect=_capture)
        server.session = mock_session
        _servers["call_lifecycle_srv"] = server

        try:
            assert server._pending_call_tool_name is None  # sanity: pre-call
            handler = _make_tool_handler("call_lifecycle_srv", "reader", 30)
            with _patch_mcp_loop_run_directly():
                handler({})
        finally:
            _servers.pop("call_lifecycle_srv", None)

        assert seen_during_call["tool_name"] == "reader"
        # The more important half: if the finally-clear is deleted, this
        # stays "reader" and a later elicitation with no call in flight
        # would silently inherit a stale tool's hint.
        assert server._pending_call_tool_name is None


class TestEndToEndCaptureThroughDecision:
    """Fix-round Finding 2: T12's verify line ("a fake listing with
    readOnlyHint: true cannot reach auto_approved=True, including through
    the elicitation gate") needs one test that spans all three stages for
    real: Task 5's capture at tool registration, this task's threading via
    the genuine _pending_call_tool_name set inside _call(), and a genuine
    request_elicitation_consent call reached only through the elicitation
    handler -- never a literal read_only_hint kwarg, never a stubbed
    request_elicitation_consent. Deleting the fake-listing setup would
    change mcp_tool_read_only_hint's answer to None and this test would
    catch that via the audit-line assertion.
    """

    def test_capture_and_a_real_call_drive_a_real_denial_with_the_hint_logged(
        self, monkeypatch, caplog,
    ):
        from tools.mcp_tool import ElicitationHandler, _make_tool_handler, _servers
        import tools.approval as approval_module

        server = MCPServerTask("e2e_full_srv")
        # Stage 1 (Task 5): the server's own tool listing declares
        # readOnlyHint: true. This is the "fake listing" the spec's verify
        # line names.
        server._tools = [_tool("reader", SimpleNamespace(readOnlyHint=True))]
        with patch("tools.registry.registry", ToolRegistry()):
            _register_server_tools("e2e_full_srv", server, {})

        handler = ElicitationHandler("e2e_full_srv", {"timeout": 5}, owner=server)
        outcome = {}

        async def _capture(name, arguments=None):
            # Stage 2 (this task): fired from "inside" session.call_tool,
            # while server._pending_call_tool_name is genuinely "reader" --
            # set by _call()'s own set line, not poked by the test. This
            # mirrors production: the MCP recv loop fires elicitation/create
            # while the tool call is still in flight.
            outcome["result"] = await handler(context=None, params=_form_params())
            return SimpleNamespace(
                content=[SimpleNamespace(text="ok")], isError=False,
            )

        mock_session = MagicMock()
        mock_session.call_tool = AsyncMock(side_effect=_capture)
        server.session = mock_session
        _servers["e2e_full_srv"] = server

        monkeypatch.setattr(approval_module, "_is_gateway_approval_context",
                            lambda: False)
        monkeypatch.setattr(approval_module, "prompt_dangerous_approval",
                            lambda *a, **k: "deny")

        try:
            with caplog.at_level("INFO", logger="tools.approval"), \
                 _patch_mcp_loop_run_directly():
                tool_handler = _make_tool_handler("e2e_full_srv", "reader", 30)
                tool_handler({})
        finally:
            _servers.pop("e2e_full_srv", None)

        # Stage 3: the real decision. A readOnlyHint: true listing did not
        # turn the stubbed denial into an approval.
        assert outcome["result"].action == "decline"

        lines = [r.getMessage() for r in caplog.records
                 if "action-tags surface=mcp_elicitation" in r.getMessage()]
        assert len(lines) == 1
        assert "read_only_hint=True" in lines[0]
