"""Gateway contract and live dispatch for /approvals."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

import gateway.run as gateway_run
from gateway.config import Platform
from gateway.platforms.base import MessageEvent
from gateway.session import SessionSource


def _event(text: str = "/approvals") -> MessageEvent:
    return MessageEvent(
        text=text,
        source=SessionSource(
            platform=Platform.TELEGRAM,
            user_id="user-1",
            chat_id="chat-1",
            chat_type="dm",
        ),
    )


def _runner():
    runner = object.__new__(gateway_run.GatewayRunner)
    runner.config = SimpleNamespace(platforms={})
    runner.hooks = MagicMock(loaded_hooks=[])
    runner.hooks.emit = AsyncMock(return_value=[])
    runner._running_agents = {}
    runner._get_or_create_gateway_honcho = lambda _key: (None, None)
    runner._is_user_authorized = lambda _source: True
    runner.session_store = SimpleNamespace(get_or_create_session=lambda _source: None)
    return runner


@pytest.mark.asyncio
async def test_gateway_rejects_non_admin_persistent_approval_change():
    runner = _runner()
    runner.config = SimpleNamespace(
        platforms={
            Platform.TELEGRAM: SimpleNamespace(
                extra={
                    "allow_admin_from": ["admin-1"],
                    "user_allowed_commands": ["approvals"],
                }
            )
        }
    )

    with patch("hermes_cli.approval_mode.run_approval_mode_command") as run:
        output = await runner._handle_approvals_command(_event("/approvals off"))

    assert "admin" in output.lower()
    run.assert_not_called()


@pytest.mark.asyncio
async def test_gateway_tags_dispatch_round_trips_as_list(tmp_path, monkeypatch):
    """G11: the gateway /approvals tags surface writes a list and reads back."""
    from gateway.slash_commands import GatewaySlashCommandsMixin
    from tools.approval import _get_auto_approve_tags

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_MANAGED_DIR", str(home / "missing-managed"))
    from hermes_cli import managed_scope
    from hermes_cli.config import _LOAD_CONFIG_CACHE, _RAW_CONFIG_CACHE
    _LOAD_CONFIG_CACHE.clear()
    _RAW_CONFIG_CACHE.clear()
    managed_scope.invalidate_managed_cache()

    handler = object.__new__(GatewaySlashCommandsMixin)  # type: ignore[attr-defined]
    handler.config = SimpleNamespace(  # type: ignore[attr-defined]
        platforms={
            Platform.TELEGRAM: SimpleNamespace(
                extra={"allow_admin_from": ["admin-1"], "user_allowed_commands": ["approvals"]}
            )
        }
    )
    from gateway.slash_access import policy_for_source
    handler.policy = lambda source: policy_for_source(handler.config, source)  # type: ignore[attr-defined]

    # Admin user on the gateway surface.
    source = SessionSource(
        platform=Platform.TELEGRAM, user_id="admin-1",
        chat_id="chat-1", chat_type="dm",
    )
    event = MessageEvent(text="/approvals tags enable pkg.install", source=source)

    output = await handler._handle_approvals_command(event)
    assert "pkg.install" in output and "enabled" in output
    assert _get_auto_approve_tags() == frozenset({"pkg.install"})

    saved = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert saved["approvals"]["auto_approve_tags"] == ["pkg.install"]

    # Non-admin gateway user is refused for tags too (R3-16: the admin gate
    # fires on any argument).
    event2 = MessageEvent(
        text="/approvals tags enable vcs.write",
        source=SessionSource(
            platform=Platform.TELEGRAM, user_id="user-1",
            chat_id="chat-1", chat_type="dm",
        ),
    )
    output2 = await handler._handle_approvals_command(event2)
    assert "admin" in output2.lower()
    assert _get_auto_approve_tags() == frozenset({"pkg.install"})


