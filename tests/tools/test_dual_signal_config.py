"""Tests for the dual-signal config surface (T4) — normaliser, getters, warnings.

Covers the D16 fail-closed normalisation, the D11 non-list rejection, the
last-known-good (G13) behaviour, and the post-write dependency warnings.
"""

from unittest.mock import patch

import pytest

import tools.approval as approval_module
from hermes_cli.config import warn_auto_approve_dependencies


@pytest.fixture(autouse=True)
def _clean_state():
    approval_module._last_known_good_auto_approve = None
    approval_module._auto_approve_tags_warned.clear()
    yield
    approval_module._last_known_good_auto_approve = None
    approval_module._auto_approve_tags_warned.clear()


# ---------------------------------------------------------------------------
# _normalize_auto_approve (D16)
# ---------------------------------------------------------------------------

class TestNormalizeAutoApprove:
    @pytest.mark.parametrize("value,expected", [
        ("legacy", "legacy"),
        ("dual_signal", "dual_signal"),
        ("off", "off"),
        ("false", "off"),
        ("no", "off"),
        ("true", "dual_signal"),
        ("yes", "dual_signal"),
        ("on", "dual_signal"),
        (False, "off"),
        (True, "dual_signal"),
        ("", "off"),
        (None, "off"),
        ("bogus", "off"),
        (["legacy"], "off"),      # non-scalar
        ({"a": 1}, "off"),
    ])
    def test_normalisation_table(self, value, expected):
        assert approval_module._normalize_auto_approve(value) == expected


# ---------------------------------------------------------------------------
# _get_auto_approve_mode (G13, D16)
# ---------------------------------------------------------------------------

class TestGetAutoApproveMode:
    def test_default_is_legacy(self):
        with patch.object(approval_module, "_get_approval_config", return_value={}):
            assert approval_module._get_auto_approve_mode() == "legacy"

    def test_read_failure_keeps_last_known_good(self):
        with patch.object(approval_module, "_get_approval_config",
                          return_value={"auto_approve": "dual_signal"}):
            assert approval_module._get_auto_approve_mode() == "dual_signal"
        # Now the read fails: must NOT fall back to permissive legacy.
        with patch.object(approval_module, "_get_approval_config",
                          side_effect=RuntimeError("io error")):
            assert approval_module._get_auto_approve_mode() == "dual_signal"

    def test_read_failure_without_history_returns_legacy(self):
        with patch.object(approval_module, "_get_approval_config",
                          side_effect=RuntimeError("io error")):
            assert approval_module._get_auto_approve_mode() == "legacy"


# ---------------------------------------------------------------------------
# _get_auto_approve_tags (D11, G13)
# ---------------------------------------------------------------------------

class TestGetAutoApproveTags:
    def test_empty_default(self):
        with patch.object(approval_module, "_get_approval_config",
                          return_value={}):
            assert approval_module._get_auto_approve_tags() == frozenset()

    def test_valid_entries_kept(self):
        with patch.object(approval_module, "_get_approval_config",
                          return_value={"auto_approve_tags": ["proc.control", "vcs.write"]}):
            assert approval_module._get_auto_approve_tags() == frozenset(
                {"proc.control", "vcs.write"}
            )

    def test_never_auto_approvable_dropped(self):
        with patch.object(approval_module, "_get_approval_config",
                          return_value={"auto_approve_tags": ["config.write", "proc.control"]}):
            tags = approval_module._get_auto_approve_tags()
            assert tags == frozenset({"proc.control"})

    def test_not_wired_dropped(self):
        with patch.object(approval_module, "_get_approval_config",
                          return_value={"auto_approve_tags": ["mcp.tool"]}):
            assert approval_module._get_auto_approve_tags() == frozenset()

    def test_non_list_string_rejected_wholesale(self):
        """D11: a scalar string must never be iterated char-by-char."""
        with patch.object(approval_module, "_get_approval_config",
                          return_value={"auto_approve_tags": "proc.control"}):
            assert approval_module._get_auto_approve_tags() == frozenset()

    def test_read_failure_returns_empty(self):
        with patch.object(approval_module, "_get_approval_config",
                          side_effect=RuntimeError("io")):
            assert approval_module._get_auto_approve_tags() == frozenset()


# ---------------------------------------------------------------------------
# _warn_auto_approve_dependencies (T4)
# ---------------------------------------------------------------------------

class TestWarnAutoApproveDependencies:
    def _cfg(self, auto_approve="legacy", mode="smart", tags=None, subagent=False):
        cfg = {
            "approvals": {"auto_approve": auto_approve, "mode": mode,
                          "auto_approve_tags": tags or []},
            "delegation": {"subagent_auto_approve": subagent},
        }
        return cfg

    def test_non_auto_approve_key_returns_silently(self, capsys):
        warn_auto_approve_dependencies("approvals.mode", self._cfg())
        assert capsys.readouterr().out == ""

    def test_dual_signal_zero_tags_warns(self, capsys):
        warn_auto_approve_dependencies("approvals.auto_approve", self._cfg("dual_signal"))
        out = capsys.readouterr().out
        assert "dual_signal" in out and "no" in out.lower()

    def test_dual_signal_with_tags_no_tag_warning(self, capsys):
        cfg = self._cfg("dual_signal", tags=["proc.control"])
        warn_auto_approve_dependencies("approvals.auto_approve", cfg)
        assert "no auto_approve_tags enabled" not in capsys.readouterr().out

    def test_dual_signal_under_manual_mode_warns(self, capsys):
        warn_auto_approve_dependencies(
            "approvals.auto_approve", self._cfg("dual_signal", mode="manual")
        )
        assert "approvals.mode" in capsys.readouterr().out

    def test_dual_signal_with_subagent_auto_approve_warns(self, capsys):
        warn_auto_approve_dependencies(
            "approvals.auto_approve", self._cfg("dual_signal", subagent=True)
        )
        assert "subagent_auto_approve" in capsys.readouterr().out

    def test_legacy_no_warnings(self, capsys):
        warn_auto_approve_dependencies("approvals.auto_approve", self._cfg())
        assert capsys.readouterr().out == ""
