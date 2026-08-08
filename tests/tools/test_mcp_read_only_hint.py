"""T12: MCP readOnlyHint captured at discovery, as an observation only.

The hint is self-declaration by an untrusted server. It may only ever add
friction: it is never an input to evaluate_dual_signal, and mcp.tool is in
NOT_WIRED, so no listing can make anything auto-approve.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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
