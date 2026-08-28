"""Fork-only: self-check-enforcer and quality-gate are mandatory.

Upstream's plugins-are-opt-in migration (config v20->21, see
config_migrations.py::_migrate_to_21) does not grandfather bundled plugins,
so on a fresh install (or any install that has run that migration) every
bundled standalone plugin sits inert under ``plugins.enabled`` until a user
opts in by hand. That defeats the point of shipping this fork's two flagship
safety gates (self-check-enforcer, quality-gate) at all -- see the "no false
done" premise in the README.

``hermes_cli.plugins.FORK_MANDATORY_PLUGIN_KEYS`` is a narrow, fork-only
carve-out in the loader (``PluginManager._discover_and_load_inner``): these
two plugin IDs load unconditionally, ignoring ``plugins.enabled`` /
``plugins.disabled`` entirely. This file proves that end to end:

* a fresh install (no config.yaml at all) loads both without any manual
  enable step;
* explicitly disabling either via config still results in it being active
  at load time;
* ``hermes plugins disable`` (CLI and dashboard paths) refuses instead of
  silently no-opping;
* ``hermes plugins list`` reports them as "mandatory", not "enabled" /
  "not enabled" / "disabled" (config doesn't actually control them).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from hermes_cli.plugins import FORK_MANDATORY_PLUGIN_KEYS, PluginManager

MANDATORY_KEYS = ("self-check-enforcer", "quality-gate")


# ── Loader level: PluginManager.discover_and_load ──────────────────────────


class TestMandatoryPluginsLoadUnconditionally:
    def test_constant_contains_both_fork_flagship_plugins(self):
        assert FORK_MANDATORY_PLUGIN_KEYS == {"self-check-enforcer", "quality-gate"}

    def test_fresh_install_loads_both_without_any_config(self, tmp_path, monkeypatch):
        """No config.yaml at all -> plugins.enabled is unset (opt-in default:
        nothing loads) -- except the two mandatory IDs, which must load
        anyway. Mirrors the `hermes plugins list` fresh-HERMES_HOME check
        from the investigation that flagged this gap."""
        hermes_home = tmp_path / "hermes_home"
        hermes_home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(hermes_home))

        mgr = PluginManager()
        mgr.discover_and_load()

        for key in MANDATORY_KEYS:
            assert key in mgr._plugins, f"{key} was not even discovered"
            entry = mgr._plugins[key]
            assert entry.enabled is True, (
                f"{key} must be active on a fresh install with zero config; "
                f"got enabled={entry.enabled} error={entry.error!r}"
            )

    def test_explicit_disable_does_not_prevent_load(self, tmp_path, monkeypatch):
        """A plugin config that explicitly tries to disable either mandatory
        plugin must still result in it being active at load time."""
        hermes_home = tmp_path / "hermes_home"
        hermes_home.mkdir()
        (hermes_home / "config.yaml").write_text(
            yaml.safe_dump(
                {"plugins": {"disabled": list(MANDATORY_KEYS), "enabled": []}}
            )
        )
        monkeypatch.setenv("HERMES_HOME", str(hermes_home))

        mgr = PluginManager()
        mgr.discover_and_load()

        for key in MANDATORY_KEYS:
            entry = mgr._plugins[key]
            assert entry.enabled is True, (
                f"{key} listed in plugins.disabled must still load on this "
                f"fork; got enabled={entry.enabled} error={entry.error!r}"
            )

    def test_empty_enabled_allowlist_does_not_prevent_load(self, tmp_path, monkeypatch):
        """plugins.enabled: [] normally means 'nothing loads' for standalone
        plugins -- the mandatory carve-out must still bypass it."""
        hermes_home = tmp_path / "hermes_home"
        hermes_home.mkdir()
        (hermes_home / "config.yaml").write_text(
            yaml.safe_dump({"plugins": {"enabled": []}})
        )
        monkeypatch.setenv("HERMES_HOME", str(hermes_home))

        mgr = PluginManager()
        mgr.discover_and_load()

        for key in MANDATORY_KEYS:
            entry = mgr._plugins[key]
            assert entry.enabled is True

    def test_an_ordinary_standalone_plugin_stays_opt_in(self, tmp_path, monkeypatch):
        """Regression guard: the carve-out must be scoped to exactly the two
        mandatory IDs, not opt-in-by-default for every bundled plugin."""
        hermes_home = tmp_path / "hermes_home"
        hermes_home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(hermes_home))

        mgr = PluginManager()
        mgr.discover_and_load()

        # security-guidance ships bundled + standalone, is not in the
        # mandatory set, and plugins.enabled was never set -> stays inert.
        assert "security-guidance" not in FORK_MANDATORY_PLUGIN_KEYS
        if "security-guidance" in mgr._plugins:
            assert mgr._plugins["security-guidance"].enabled is False


# ── CLI level: hermes plugins disable ───────────────────────────────────────


class TestCmdDisableRefusesMandatoryPlugins:
    @pytest.mark.parametrize("key", MANDATORY_KEYS)
    @patch("hermes_cli.plugins_cmd._save_disabled_set")
    @patch("hermes_cli.plugins_cmd._save_enabled_set")
    @patch("hermes_cli.plugins_cmd._get_disabled_set", return_value=set())
    @patch("hermes_cli.plugins_cmd._get_enabled_set", return_value=set())
    def test_cmd_disable_exits_nonzero_and_writes_nothing(
        self, mock_en, mock_dis, mock_save_en, mock_save_dis, key,
    ):
        from hermes_cli.plugins_cmd import cmd_disable

        with pytest.raises(SystemExit) as excinfo:
            cmd_disable(key)

        assert excinfo.value.code != 0
        mock_save_en.assert_not_called()
        mock_save_dis.assert_not_called()

    @pytest.mark.parametrize("key", MANDATORY_KEYS)
    def test_dashboard_disable_returns_ok_false(self, key):
        from hermes_cli.plugins_cmd import dashboard_set_agent_plugin_enabled

        with patch("hermes_cli.plugins_cmd._save_disabled_set") as mock_save_dis, \
             patch("hermes_cli.plugins_cmd._save_enabled_set") as mock_save_en:
            result = dashboard_set_agent_plugin_enabled(key, enabled=False)

        assert result["ok"] is False
        assert "error" in result
        mock_save_en.assert_not_called()
        mock_save_dis.assert_not_called()

    @pytest.mark.parametrize("key", MANDATORY_KEYS)
    def test_dashboard_enable_still_works(self, key):
        """Enabling (a harmless no-op given the carve-out) is not blocked --
        only the misleading disable path is refused."""
        from hermes_cli.plugins_cmd import dashboard_set_agent_plugin_enabled

        with patch("hermes_cli.plugins_cmd._save_disabled_set"), \
             patch("hermes_cli.plugins_cmd._save_enabled_set"), \
             patch("hermes_cli.plugins_cmd._toggle_plugin_toolset"):
            result = dashboard_set_agent_plugin_enabled(key, enabled=True)

        assert result["ok"] is True


# ── hermes plugins list — status reporting ──────────────────────────────────


class TestPluginStatusReportsMandatory:
    @pytest.mark.parametrize("key", MANDATORY_KEYS)
    def test_status_is_mandatory_even_when_config_says_disabled(self, key):
        from hermes_cli.plugins_cmd import _plugin_status

        status = _plugin_status(key, enabled=set(), disabled={key})
        assert status == "mandatory"

    @pytest.mark.parametrize("key", MANDATORY_KEYS)
    def test_status_is_mandatory_with_no_config_at_all(self, key):
        from hermes_cli.plugins_cmd import _plugin_status

        status = _plugin_status(key, enabled=set(), disabled=set())
        assert status == "mandatory"

    def test_enabled_filter_includes_mandatory_plugins(self):
        """`hermes plugins list --enabled` must still surface the mandatory
        pair even though their status string is 'mandatory', not 'enabled'."""
        from types import SimpleNamespace

        from hermes_cli.plugins_cmd import _filter_plugin_entries

        entries = [
            ("self-check-enforcer", "3.7.3", "desc", "bundled", Path("/x"), "self-check-enforcer"),
            ("quality-gate", "1.0.0", "desc", "bundled", Path("/y"), "quality-gate"),
            ("other-plugin", "1.0.0", "desc", "bundled", Path("/z"), "other-plugin"),
        ]
        args = SimpleNamespace(enabled=True)
        filtered = _filter_plugin_entries(entries, args, enabled=set(), disabled=set())
        keys = {entry[5] for entry in filtered}
        assert keys == {"self-check-enforcer", "quality-gate"}
