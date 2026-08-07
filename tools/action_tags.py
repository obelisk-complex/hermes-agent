"""Stable action-tag taxonomy for the dual-signal auto-approval gate.

Every gated action resolves to exactly one :class:`ActionTag`. The enum values
are **config surface**: they appear in ``config.yaml`` (``approvals.auto_approve_tags``)
and in audit lines, so they must never be renamed — only added, or deprecated with
an alias. This module imports nothing from Hermes (D2 of the dual-signal plan):
anything needing ``approval.py`` internals lives in a wrapper there.

Sets:
- ``CONFIGURABLE_TAGS`` — tags a user may enable for auto-approval.
- ``NEVER_AUTO_APPROVABLE`` — tags that can never be auto-approved, excluded from
  ``CONFIGURABLE_TAGS`` (config writes, privilege escalation, tirith findings,
  arbitrary local Python, parser-limit failures, unresolved).
- ``NOT_WIRED`` — tags on surfaces that have no author verdict today (no
  auto-approval path exists); listed as "not wired" by the ``/approvals tags`` UI.

The ``ActionNature`` record is populated and exposed from day one but read by
nothing yet: it is the future policy engine's input, replacing the boolean
verdict (see the design plan, "the boolean is a stopgap"). ``None`` fields mean
"not yet classified" and are treated as unsafe by any future consumer.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Literal

__all__ = [
    "ActionTag",
    "ActionNature",
    "ACTION_NATURE",
    "DANGEROUS_PATTERN_TAGS",
    "DETECTION_FAMILY_TAGS",
    "CONFIGURABLE_TAGS",
    "NEVER_AUTO_APPROVABLE",
    "NOT_WIRED",
    "tag_for_pattern_key",
]


class ActionTag(Enum):
    """Frozen taxonomy of gated-action categories. Values are stable strings."""

    # -- configurable: reachable from a surface with an author verdict ----------
    COMMAND_EXEC = "command.exec"          # dangerous pattern, no more specific tag
    COMMAND_DELETE = "command.delete"      # rm, del/Remove-Item, recursive delete, find -delete
    COMMAND_PERMS = "command.perms"        # chmod/chown
    COMMAND_DISK = "command.disk"          # mkfs, dd, partition tools
    COMMAND_INTERPRETER = "command.interpreter"  # _execution_flag_findings family
    COMMAND_TOOL_EXEC = "command.tool_exec"      # dynamic "arbitrary program execution via <tool> <opt>"
    PROC_CONTROL = "proc.control"          # kill, systemctl, service, launchd/gateway lifecycle
    NET_EGRESS = "net.egress"              # curl/wget/pipe-to-interpreter/outbound
    PKG_INSTALL = "pkg.install"            # pip/npm/apt install
    VCS_WRITE = "vcs.write"                # git push force variants, reset --hard, branch force delete
    FILE_WRITE = "file.write"              # _SENSITIVE_WRITE_TARGET family (after the D14 override)
    SECRET_READ = "secret.read"            # ~/.ssh, .env, credential-file reads

    # -- never auto-approvable --------------------------------------------------
    CONFIG_WRITE = "config.write"          # the Hermes home and the policy it holds (G8)
    PRIV_ESCALATE = "priv.escalate"        # sudo privilege flags, PowerShell encoded commands (D15)
    SECURITY_SCAN = "security.scan"        # tirith:* findings (G9)
    CODE_EXEC = "code.exec"                # execute_code guard — arbitrary local Python (D9)
    PARSER_LIMIT = "parser.limit"          # _PARSER_LIMIT_DESCRIPTION / _MALFORMED_EXEC_DESCRIPTION
    UNTAGGED = "UNTAGGED"                  # anything unresolved — fail-closed default

    # -- not wired: no author verdict exists on the surface ---------------------
    PLUGIN_RULE = "plugin.rule"            # _run_approval_gate plugin_rule:* keys
    MEMORY_WRITE = "memory.write"          # tools/write_approval.py MEMORY subsystem
    SKILL_WRITE = "skill.write"            # tools/write_approval.py SKILLS subsystem
    MCP_TOOL = "mcp.tool"                  # MCP tool invocation / elicitation gate
    COMPUTER_USE = "computer.use"          # tools/computer_use/tool.py ladder


@dataclass(frozen=True)
class ActionNature:
    """Machine-readable nature of an action, for the future policy engine.

    ``kind`` is ``"content"`` (writes/reads free-form content) vs ``"switch"``
    (flips a state) — the two classes differ in data-leak risk.
    """

    destructive: bool | None
    reversible: bool | None
    kind: Literal["content", "switch", None]


ACTION_NATURE: dict[ActionTag, ActionNature] = {
    ActionTag.COMMAND_EXEC:      ActionNature(None, False, "switch"),
    ActionTag.COMMAND_DELETE:    ActionNature(True, False, "switch"),
    ActionTag.COMMAND_PERMS:     ActionNature(True, True, "switch"),
    ActionTag.COMMAND_DISK:      ActionNature(True, False, "switch"),
    ActionTag.COMMAND_INTERPRETER: ActionNature(None, False, "switch"),
    ActionTag.COMMAND_TOOL_EXEC: ActionNature(None, False, "switch"),
    ActionTag.PROC_CONTROL:      ActionNature(True, True, "switch"),
    ActionTag.NET_EGRESS:        ActionNature(False, None, "switch"),
    ActionTag.PKG_INSTALL:       ActionNature(False, True, "switch"),
    ActionTag.VCS_WRITE:         ActionNature(True, None, "content"),
    ActionTag.FILE_WRITE:        ActionNature(True, False, "content"),
    ActionTag.SECRET_READ:       ActionNature(False, None, "content"),
    ActionTag.CONFIG_WRITE:      ActionNature(True, False, "content"),
    ActionTag.PRIV_ESCALATE:     ActionNature(True, False, "switch"),
    ActionTag.SECURITY_SCAN:     ActionNature(None, None, "switch"),
    ActionTag.CODE_EXEC:         ActionNature(None, False, "switch"),
    ActionTag.PARSER_LIMIT:      ActionNature(None, None, None),
    ActionTag.UNTAGGED:          ActionNature(None, None, None),
    ActionTag.PLUGIN_RULE:       ActionNature(None, False, "switch"),
    ActionTag.MEMORY_WRITE:      ActionNature(False, True, "content"),
    ActionTag.SKILL_WRITE:       ActionNature(False, True, "content"),
    ActionTag.MCP_TOOL:          ActionNature(None, False, "switch"),
    ActionTag.COMPUTER_USE:      ActionNature(None, False, "switch"),
}

NEVER_AUTO_APPROVABLE: frozenset[str] = frozenset({
    ActionTag.CONFIG_WRITE.value,
    ActionTag.PRIV_ESCALATE.value,
    ActionTag.SECURITY_SCAN.value,
    ActionTag.CODE_EXEC.value,
    ActionTag.PARSER_LIMIT.value,
    ActionTag.UNTAGGED.value,
})

NOT_WIRED: frozenset[str] = frozenset({
    ActionTag.PLUGIN_RULE.value,
    ActionTag.MEMORY_WRITE.value,
    ActionTag.SKILL_WRITE.value,
    ActionTag.MCP_TOOL.value,
    ActionTag.COMPUTER_USE.value,
})

CONFIGURABLE_TAGS: frozenset[str] = frozenset(
    tag.value for tag in ActionTag
    if tag.value not in NEVER_AUTO_APPROVABLE and tag.value not in NOT_WIRED
)

# ---------------------------------------------------------------------------
# T2 — pattern-key -> tag map.
#
# Keyed by the **canonical description string**, which is exactly what
# `detect_dangerous_command` returns as `pattern_key` (approval.py:2185/2193/2197).
# The completeness test in tests/tools/test_action_tags.py derives the domain
# from DANGEROUS_PATTERNS at runtime, so a new pattern without a tag fails CI.
# D12: no alias canonicalisation — legacy regex-derived keys never reach the
# tag layer (they are consumed by is_approved before tagging).
# ---------------------------------------------------------------------------

# Description shared by two patterns:
#   "start gateway outside systemd (use 'systemctl --user restart hermes-gateway')"
DANGEROUS_PATTERN_TAGS: dict[str, ActionTag] = {
    # deletes
    "delete in root path": ActionTag.COMMAND_DELETE,
    "recursive delete": ActionTag.COMMAND_DELETE,
    "recursive delete (long flag)": ActionTag.COMMAND_DELETE,
    "recursive delete (flags after operands)": ActionTag.COMMAND_DELETE,
    "Windows cmd destructive delete": ActionTag.COMMAND_DELETE,
    "Windows PowerShell destructive delete": ActionTag.COMMAND_DELETE,
    "xargs with rm": ActionTag.COMMAND_DELETE,
    "find -exec/-execdir rm": ActionTag.COMMAND_DELETE,
    "find -delete": ActionTag.COMMAND_DELETE,
    # permissions
    "world/other-writable permissions": ActionTag.COMMAND_PERMS,
    "recursive world/other-writable (long flag)": ActionTag.COMMAND_PERMS,
    "recursive chown to root": ActionTag.COMMAND_PERMS,
    "recursive chown to root (long flag)": ActionTag.COMMAND_PERMS,
    "chmod +x followed by immediate execution": ActionTag.COMMAND_PERMS,
    # disk
    "format filesystem": ActionTag.COMMAND_DISK,
    "disk copy": ActionTag.COMMAND_DISK,
    "write to block device": ActionTag.COMMAND_DISK,
    # SQL / catch-all command.exec
    "SQL DROP": ActionTag.COMMAND_EXEC,
    "SQL DELETE without WHERE": ActionTag.COMMAND_EXEC,
    "SQL TRUNCATE": ActionTag.COMMAND_EXEC,
    "fork bomb": ActionTag.COMMAND_EXEC,
    # egress
    "pipe remote content to shell": ActionTag.NET_EGRESS,
    "execute remote script via process substitution": ActionTag.NET_EGRESS,
    "execute remote content via command substitution": ActionTag.NET_EGRESS,
    "pipe decoded content to shell (possible command obfuscation)": ActionTag.NET_EGRESS,
    "pipe xxd-decoded content to shell (possible command obfuscation)": ActionTag.NET_EGRESS,
    "pipe tr-transformed output to shell (possible command obfuscation)": ActionTag.NET_EGRESS,
    "pipe openssl-decoded content to shell (possible command obfuscation)": ActionTag.NET_EGRESS,
    # docker/podman daemon redirects (remote daemon takeover) — no more specific tag
    "docker with remote daemon redirect (-H/--host)": ActionTag.COMMAND_EXEC,
    "docker with daemon redirect (--context: alternate daemon)": ActionTag.COMMAND_EXEC,
    "docker context use (switches default daemon for future commands)": ActionTag.COMMAND_EXEC,
    "podman with remote daemon redirect (--url/--connection/--identity)": ActionTag.COMMAND_EXEC,
    "podman remote mode (-r/--remote: remote daemon)": ActionTag.COMMAND_EXEC,
    "docker/podman daemon redirect via environment (DOCKER_HOST/CONTAINER_HOST)": ActionTag.COMMAND_EXEC,
    # process/daemon lifecycle
    "stop/restart system service": ActionTag.PROC_CONTROL,
    "kill all processes": ActionTag.PROC_CONTROL,
    "force kill processes": ActionTag.PROC_CONTROL,
    "force kill processes (killall -KILL)": ActionTag.PROC_CONTROL,
    "force kill processes (killall -s KILL)": ActionTag.PROC_CONTROL,
    "kill processes by regex (killall -r)": ActionTag.PROC_CONTROL,
    "stop/restart hermes gateway (kills running agents)": ActionTag.PROC_CONTROL,
    "hermes update (restarts gateway, kills running agents)": ActionTag.PROC_CONTROL,
    "docker compose restart/stop/kill/down (container lifecycle)": ActionTag.PROC_CONTROL,
    "docker restart/stop/kill (container lifecycle)": ActionTag.PROC_CONTROL,
    "start gateway outside systemd (use 'systemctl --user restart hermes-gateway')": ActionTag.PROC_CONTROL,
    "kill hermes/gateway process (self-termination)": ActionTag.PROC_CONTROL,
    "kill process via pgrep/pidof expansion (self-termination)": ActionTag.PROC_CONTROL,
    "kill process via backtick pgrep/pidof expansion (self-termination)": ActionTag.PROC_CONTROL,
    "stop/restart hermes launchd service (kills running agents)": ActionTag.PROC_CONTROL,
    # file writes
    "overwrite system file via tee": ActionTag.FILE_WRITE,
    "overwrite system file via redirection": ActionTag.FILE_WRITE,
    "overwrite system config": ActionTag.FILE_WRITE,
    "in-place edit of system config": ActionTag.FILE_WRITE,
    "in-place edit of system config (long flag)": ActionTag.FILE_WRITE,
    "copy/move file into system config path": ActionTag.FILE_WRITE,
    "copy/move file into sensitive credential/SSH/shell-rc path": ActionTag.FILE_WRITE,
    "in-place edit of sensitive credential/SSH/shell-rc path": ActionTag.FILE_WRITE,
    "in-place edit of sensitive credential/SSH/shell-rc path (long flag)": ActionTag.FILE_WRITE,
    "in-place edit of sensitive credential/SSH/shell-rc path (perl/ruby)": ActionTag.FILE_WRITE,
    # config writes (static; the D14 override adds the dynamic Hermes-home route)
    "overwrite project env/config via tee": ActionTag.CONFIG_WRITE,
    "overwrite project env/config via redirection": ActionTag.CONFIG_WRITE,
    "overwrite project env/config file": ActionTag.CONFIG_WRITE,
    "in-place edit of Hermes config/env": ActionTag.CONFIG_WRITE,
    "in-place edit of Hermes config/env (long flag)": ActionTag.CONFIG_WRITE,
    "in-place edit of Hermes config/env (perl/ruby)": ActionTag.CONFIG_WRITE,
    # T2a (D19): hermes config CLI write verbs — tagged statically
    "write hermes config via CLI (approval policy lives here)": ActionTag.CONFIG_WRITE,
    # interpreters / heredoc shell
    "shell execution via heredoc": ActionTag.COMMAND_INTERPRETER,
    # vcs
    "git reset --hard (destroys uncommitted changes)": ActionTag.VCS_WRITE,
    "git force push (rewrites remote history)": ActionTag.VCS_WRITE,
    "git force push short flag (rewrites remote history)": ActionTag.VCS_WRITE,
    "git clean with force (deletes untracked files)": ActionTag.VCS_WRITE,
    "git branch force delete": ActionTag.VCS_WRITE,
    "git branch force delete (long flags)": ActionTag.VCS_WRITE,
    "git branch force delete (long flags, force-first)": ActionTag.VCS_WRITE,
    # privilege escalation (D15)
    "sudo with privilege flag (stdin/askpass/shell/list)": ActionTag.PRIV_ESCALATE,
    "sudo with combined-flag privilege escalation": ActionTag.PRIV_ESCALATE,
    "PowerShell encoded command execution": ActionTag.PRIV_ESCALATE,
}

# Literals emitted by _execution_flag_findings / the parser-limit family
# (approval.py:1257-1258, 1641-1675). Pinned here so T2's corpus test can
# fail CI when a new detection family ships without a tag.
DETECTION_FAMILY_TAGS: dict[str, ActionTag] = {
    "command parser limit exceeded": ActionTag.PARSER_LIMIT,
    "command parser limit or malformed executable payload": ActionTag.PARSER_LIMIT,
    "script execution via -e/-c flag": ActionTag.COMMAND_INTERPRETER,
    "script execution via heredoc": ActionTag.COMMAND_INTERPRETER,
    "shell command via -c/-lc flag": ActionTag.COMMAND_INTERPRETER,
}

# Dynamic key prefix: f"arbitrary program execution via {tool} {option}"
# (approval.py:1675) — the key space is the cross-product of tools and
# options, so only a prefix rule can cover it.
TOOL_EXEC_PREFIX = "arbitrary program execution via "


def tag_for_pattern_key(pattern_key: str) -> ActionTag:
    """Resolve a canonical detection description to its tag.

    Fail-closed: anything unknown resolves to :attr:`ActionTag.UNTAGGED`.
    Legacy regex-derived keys are never inputs here (D12) — they are consumed
    by the allowlist checks before tagging.
    """
    if pattern_key in DANGEROUS_PATTERN_TAGS:
        return DANGEROUS_PATTERN_TAGS[pattern_key]
    if pattern_key in DETECTION_FAMILY_TAGS:
        return DETECTION_FAMILY_TAGS[pattern_key]
    if pattern_key.startswith(TOOL_EXEC_PREFIX):
        return ActionTag.COMMAND_TOOL_EXEC
    return ActionTag.UNTAGGED
