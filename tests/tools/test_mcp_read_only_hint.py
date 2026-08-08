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
