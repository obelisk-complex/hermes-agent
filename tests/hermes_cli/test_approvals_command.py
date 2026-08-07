"""Cross-surface contract for the persistent /approvals mode command."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import yaml

from cli import HermesCLI
from hermes_cli.commands import (
    GATEWAY_KNOWN_COMMANDS,
    SUBCOMMANDS,
    SlashCommandCompleter,
    gateway_help_lines,
    resolve_command,
    telegram_bot_commands,
)
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document


def _completions(text: str) -> set[str]:
    return {
        item.text
        for item in SlashCommandCompleter().get_completions(
            Document(text=text), CompleteEvent(completion_requested=True)
        )
    }


def test_approvals_registry_drives_help_menu_and_autocomplete():
    command = resolve_command("approvals")
    assert command is not None
    assert command.category == "Configuration"
    assert command.args_hint == "[manual|smart|off]"
    assert SUBCOMMANDS["/approvals"] == ["manual", "smart", "off"]
    assert "approvals" in GATEWAY_KNOWN_COMMANDS
    assert any("/approvals" in line for line in gateway_help_lines())
    assert "approvals" in {name for name, _ in telegram_bot_commands()}
    assert _completions("/approvals ") == {"manual", "smart", "off"}


def _isolate_config(monkeypatch, home):
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_MANAGED_DIR", str(home / "missing-managed"))
    from hermes_cli import managed_scope
    from hermes_cli.config import _LOAD_CONFIG_CACHE, _RAW_CONFIG_CACHE

    _LOAD_CONFIG_CACHE.clear()
    _RAW_CONFIG_CACHE.clear()
    managed_scope.invalidate_managed_cache()






def test_shared_command_refuses_managed_mode_override(tmp_path, monkeypatch):
    from hermes_cli import managed_scope
    from hermes_cli.approval_mode import run_approval_mode_command

    home = tmp_path / "home"
    managed = tmp_path / "managed"
    home.mkdir()
    managed.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_MANAGED_DIR", str(managed))
    (managed / "config.yaml").write_text("approvals:\n  mode: manual\n", encoding="utf-8")
    managed_scope.invalidate_managed_cache()

    result = run_approval_mode_command("off")

    assert result.ok is False
    assert result.mode == "manual"
    assert result.changed is False
    assert "managed" in result.message.lower()
    assert not (home / "config.yaml").exists()


# ---------------------------------------------------------------------------
# /approvals tags — dual-signal tag surface (T10, G11)
# ---------------------------------------------------------------------------

def test_tags_round_trip_writes_a_list(tmp_path, monkeypatch):
    """G11: enable/disable round-trips and the value reads back as a LIST."""
    from hermes_cli.approval_mode import run_approval_tags_command
    from tools.approval import _get_auto_approve_tags

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    _isolate_config(monkeypatch, home)

    assert _get_auto_approve_tags() == frozenset()

    result = run_approval_tags_command("enable proc.control")
    assert result.ok is True
    assert _get_auto_approve_tags() == frozenset({"proc.control"})

    result = run_approval_tags_command("enable vcs.write")
    assert result.ok is True
    assert _get_auto_approve_tags() == frozenset({"proc.control", "vcs.write"})

    result = run_approval_tags_command("disable proc.control")
    assert result.ok is True
    assert _get_auto_approve_tags() == frozenset({"vcs.write"})

    # On disk it is a list, not a string (D11 / control 14).
    raw = (home / "config.yaml").read_text(encoding="utf-8")
    saved = yaml.safe_load(raw)
    assert saved["approvals"]["auto_approve_tags"] == ["vcs.write"]


def test_tags_reject_non_configurable_without_writing(tmp_path, monkeypatch):
    from hermes_cli.approval_mode import run_approval_tags_command
    from tools.approval import _get_auto_approve_tags

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    _isolate_config(monkeypatch, home)

    for bad in ("security.scan", "priv.escalate", "code.exec", "mcp.tool",
                "config.write", "UNTAGGED", "totally-unknown"):
        result = run_approval_tags_command(f"enable {bad}")
        assert result.ok is False
        assert _get_auto_approve_tags() == frozenset()

    result = run_approval_tags_command("bogus-verb proc.control")
    assert result.ok is False
    assert _get_auto_approve_tags() == frozenset()


def test_tags_listing_covers_every_tag_and_allowlist_note(tmp_path, monkeypatch):
    from hermes_cli.approval_mode import run_approval_tags_command
    from tools.action_tags import ActionTag

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    _isolate_config(monkeypatch, home)

    result = run_approval_tags_command(None)
    assert result.ok is True
    for tag in ActionTag:
        assert tag.value in result.message
    assert "command_allowlist" in result.message


def test_tags_dispatch_on_cli_surface(tmp_path, monkeypatch, capsys):
    """The CLI /approvals handler routes 'tags' before the mode runner."""
    from cli import HermesCLI

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    _isolate_config(monkeypatch, home)

    cli = HermesCLI.__new__(HermesCLI)
    cli._handle_approvals_command("/approvals tags enable net.egress")
    out = capsys.readouterr().out
    assert "net.egress" in out and "enabled" in out
    # The mode runner must not have swallowed it as an invalid mode.
    assert "Usage:" not in out






