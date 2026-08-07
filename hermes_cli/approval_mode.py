"""Shared persistent approval-mode command logic.

Approval mode is profile-scoped configuration, not conversation state. Changing
it affects subsequent terminal guard checks immediately because approval.py
loads config on each check; it must not rebuild a live agent or mutate its
system prompt/tool schema, preserving the prompt-cache prefix.
"""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from io import StringIO
from typing import Optional

VALID_APPROVAL_MODES = ("manual", "smart", "off")


@dataclass(frozen=True)
class ApprovalModeResult:
    ok: bool
    mode: str
    changed: bool
    message: str


def _effective_mode() -> str:
    """Return the exact mode enforced by the terminal approval guard."""
    from tools.approval import _get_approval_mode

    return _get_approval_mode()


# ---------------------------------------------------------------------------
# /approvals tags — dual-signal tag surface (T10 of the dual-signal plan)
# ---------------------------------------------------------------------------

def _tags_listing_lines() -> list[str]:
    """Listing body: every tag with nature, enabled state, and class.

    Header/dependency lines are added by the caller so the CLI and gateway
    surfaces can wrap the message differently. Reads live config so the
    listing reflects exactly what the gate will enforce.
    """
    from tools.action_tags import (
        CONFIGURABLE_TAGS,
        NEVER_AUTO_APPROVABLE,
        NOT_WIRED,
        ACTION_NATURE,
        ActionTag,
    )
    from tools.approval import _get_auto_approve_tags

    enabled = _get_auto_approve_tags()
    lines: list[str] = []
    for tag in ActionTag:
        value = tag.value
        if value in CONFIGURABLE_TAGS:
            cls = "configurable"
        elif value in NEVER_AUTO_APPROVABLE:
            cls = "never auto-approvable"
        elif value in NOT_WIRED:
            cls = "not wired"
        else:  # pragma: no cover — taxonomy invariant
            cls = "unknown"
        nature = ACTION_NATURE.get(tag)
        nature_s = (
            f"destructive={nature.destructive}, reversible={nature.reversible}, "
            f"kind={nature.kind}" if nature else "unclassified"
        )
        mark = "ENABLED" if value in enabled else "off"
        lines.append(f"  {value:18} {mark:7} [{cls}] {nature_s}")
    return lines


def _tag_dependency_warnings() -> list[str]:
    """T4's three dependency warnings, re-used in the listing (R13/R14/R16)."""
    warnings: list[str] = []
    try:
        from hermes_cli.config import load_config_readonly
        cfg = load_config_readonly() or {}
        approvals = cfg.get("approvals", {}) or {}
        mode = str(approvals.get("auto_approve", "legacy") or "legacy").strip().lower()
        if mode == "false":
            mode = "off"
        elif mode == "true":
            mode = "dual_signal"
        if mode == "dual_signal":
            tags = approvals.get("auto_approve_tags") or []
            if isinstance(tags, list) and len(tags) == 0:
                warnings.append(
                    "dual_signal with no tags enabled: every previously "
                    "auto-approved action now races the approval timeout."
                )
            if str(approvals.get("mode", "smart") or "smart") != "smart":
                warnings.append(
                    "auto_approve only takes effect when approvals.mode is "
                    "'smart' — the current mode is "
                    f"'{approvals.get('mode')}'."
                )
            subagent = None
            if isinstance(cfg.get("delegation"), dict):
                subagent = cfg["delegation"].get("subagent_auto_approve")
            from utils import is_truthy_value
            if is_truthy_value(subagent):
                warnings.append(
                    "delegation.subagent_auto_approve is true — CLI-parented "
                    "subagents auto-approve via their own escape hatch; the "
                    "head-of-line barrier does not apply to them."
                )
    except Exception:
        pass
    return warnings


def run_approval_tags_command(args: str | None) -> ApprovalModeResult:
    """Handle ``/approvals tags`` (T10).

    ``args`` is the raw stripped argument substring after the command name,
    or ``None`` (D18, audit R4-12). Subcommands: ``tags`` (listing),
    ``tags enable <tag>``, ``tags disable <tag>``. Only ``CONFIGURABLE_TAGS``
    values are accepted; anything else is rejected without writing config.

    Write mechanism (D11): ``load_config()`` → mutate the in-memory list →
    ``save_config(config)``, mirroring ``save_permanent_allowlist`` — NOT
    ``set_config_value`` (which cannot write list values).
    """
    from tools.action_tags import CONFIGURABLE_TAGS
    from tools.approval import _get_approval_mode, _get_auto_approve_tags

    effective_mode = _get_approval_mode()
    current = _get_auto_approve_tags()

    if not args:
        lines = _tags_listing_lines()
        header = (
            f"Approval mode: {effective_mode} · auto_approve: "
            f"{_get_auto_approve_mode_for_listing()}"
        )
        body = "\n".join(lines)
        allowlist_note = _command_allowlist_note()
        warnings = _tag_dependency_warnings()
        msg = header + "\n" + body + "\n" + allowlist_note
        if warnings:
            msg += "\n" + "\n".join(f"  ⚠ {w}" for w in warnings)
        return ApprovalModeResult(True, effective_mode, False, msg)

    parts = args.split()
    verb = parts[0].lower()
    if verb not in ("enable", "disable"):
        valid = ", ".join(sorted(CONFIGURABLE_TAGS))
        return ApprovalModeResult(
            False, effective_mode, False,
            f"Usage: /approvals tags [enable|disable <tag>]. Valid tags: {valid}",
        )
    if len(parts) < 2:
        return ApprovalModeResult(
            False, effective_mode, False, f"Usage: /approvals tags {verb} <tag>",
        )
    tag = parts[1]
    if tag not in CONFIGURABLE_TAGS:
        valid = ", ".join(sorted(CONFIGURABLE_TAGS))
        return ApprovalModeResult(
            False, effective_mode, False,
            f"Unknown or non-configurable tag '{tag}' — nothing written. "
            f"Valid tags: {valid}",
        )

    new_tags = set(current)
    if verb == "enable":
        new_tags.add(tag)
    else:
        new_tags.discard(tag)

    try:
        from hermes_cli.config import load_config, save_config
        config = load_config() or {}
        approvals = config.setdefault("approvals", {})
        approvals["auto_approve_tags"] = sorted(new_tags)
        save_config(config)
    except Exception as exc:
        return ApprovalModeResult(
            False, effective_mode, False,
            f"Failed to save auto_approve_tags: {exc}",
        )

    # Managed config: save_config prints via managed_error and RETURNS without
    # raising (config.py:3527-3529) — re-read to detect the silent non-write.
    if _get_auto_approve_tags() != frozenset(new_tags):
        return ApprovalModeResult(
            False, effective_mode, False,
            "Not saved — approvals.auto_approve_tags is managed by your "
            "administrator.",
        )
    state = "enabled" if verb == "enable" else "disabled"
    return ApprovalModeResult(
        True, effective_mode, True,
        f"Tag {tag} {state}. Now enabled: "
        + (", ".join(sorted(new_tags)) if new_tags else "(none)"),
    )


def _get_auto_approve_mode_for_listing() -> str:
    """Safe read of approvals.auto_approve for the listing header (never raises)."""
    try:
        from tools.approval import _get_auto_approve_mode
        return _get_auto_approve_mode()
    except Exception:
        return "legacy"


def _command_allowlist_note() -> str:
    """R24: allowlisted commands bypass the tag gate entirely — say so."""
    try:
        from hermes_cli.config import load_config_readonly
        cfg = load_config_readonly() or {}
        entries = cfg.get("command_allowlist") or []
        count = len(entries) if isinstance(entries, list) else 0
    except Exception:
        count = -1
    if count < 0:
        return "  command_allowlist: (unreadable)"
    if count == 0:
        return "  command_allowlist: 0 entries (nothing bypasses the tag gate)"
    return (
        f"  command_allowlist: {count} entr{'y' if count == 1 else 'ies'} — "
        "these commands bypass the dual-signal tag gate entirely (approved "
        "above it at the allowlist check)."
    )


def run_approval_mode_command(requested_mode: Optional[str]) -> ApprovalModeResult:
    """Inspect or persist ``approvals.mode`` through canonical config APIs."""
    current = _effective_mode()
    requested = (requested_mode or "").strip().lower()

    if not requested:
        return ApprovalModeResult(
            True,
            current,
            False,
            f"Approval mode: {current} (persistent profile setting).",
        )
    if requested not in VALID_APPROVAL_MODES:
        return ApprovalModeResult(
            False,
            current,
            False,
            "Usage: /approvals [manual|smart|off|tags ...]",
        )

    # set_config_value is the canonical managed-scope/write-safety chokepoint.
    # It reports managed policy through stderr + SystemExit, so capture that for
    # slash-command output instead of terminating the interactive worker.
    from hermes_cli.config import set_config_value

    output = StringIO()
    try:
        with redirect_stdout(output), redirect_stderr(output):
            set_config_value("approvals.mode", requested)
    except SystemExit:
        detail = output.getvalue().strip() or "Approval mode is managed and cannot be changed."
        return ApprovalModeResult(False, current, False, detail)
    except Exception as exc:
        return ApprovalModeResult(
            False,
            current,
            False,
            f"Failed to save approval mode: {exc}",
        )

    effective = _effective_mode()
    if effective != requested:
        return ApprovalModeResult(
            False,
            effective,
            False,
            f"Approval mode remains {effective}; the requested value did not become effective.",
        )
    return ApprovalModeResult(
        True,
        effective,
        effective != current,
        f"Approval mode: {effective} (persistent profile setting).",
    )
