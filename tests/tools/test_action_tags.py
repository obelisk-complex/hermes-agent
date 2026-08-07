"""Tests for the dual-signal action-tag taxonomy (T1-T3, T2a).

Covers:
- T1/T2: every DANGEROUS_PATTERNS description resolves to a tag; the map is
  complete by construction (derived from approval.py at runtime, so a new
  pattern without a tag fails CI — negative control 1/5).
- T2a: the `hermes config` write-verb detection entry matches the write verbs
  and rejects the read verbs (control 20).
- T3/D14: the config-write override fires on every detection variant for
  Hermes-home targets regardless of the tag the command would otherwise get
  (controls 2-4, 19).
"""

import ast
from unittest.mock import patch

import pytest

from tools.action_tags import (
    CONFIGURABLE_TAGS,
    DANGEROUS_PATTERN_TAGS,
    NEVER_AUTO_APPROVABLE,
    NOT_WIRED,
    ActionTag,
    tag_for_pattern_key,
)


def _live_dangerous_descriptions():
    """Extract the canonical description strings from approval.py's table."""
    src = open("tools/approval.py", encoding="utf-8").read()
    tree = ast.parse(src)
    descs = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "DANGEROUS_PATTERNS"
            for t in node.targets
        ):
            for elt in node.value.elts:
                d = elt.elts[1]
                if isinstance(d, ast.Constant) and isinstance(d.value, str):
                    descs.append(d.value)
    return descs


# ---------------------------------------------------------------------------
# T1/T2 — taxonomy completeness
# ---------------------------------------------------------------------------

class TestTaxonomyCompleteness:
    def test_every_pattern_description_is_tagged(self):
        """Control 1: a new pattern without a tag must fail CI."""
        live = set(_live_dangerous_descriptions())
        # T2a's entry is in the live table too (it was added in this feature).
        missing = live - set(DANGEROUS_PATTERN_TAGS)
        assert not missing, f"untagged descriptions: {sorted(missing)}"

    def test_tag_values_are_stable_strings(self):
        for tag in ActionTag:
            assert isinstance(tag.value, str) and tag.value

    def test_sets_are_disjoint_and_cover_every_tag(self):
        configured = set(t.value for t in ActionTag)
        assert NEVER_AUTO_APPROVABLE.isdisjoint(NOT_WIRED)
        assert configured == NEVER_AUTO_APPROVABLE | NOT_WIRED | CONFIGURABLE_TAGS

    def test_never_auto_approvable_excluded_from_configurable(self):
        assert NEVER_AUTO_APPROVABLE.isdisjoint(CONFIGURABLE_TAGS)

    def test_not_wired_excluded_from_configurable(self):
        assert NOT_WIRED.isdisjoint(CONFIGURABLE_TAGS)

    def test_configurable_contains_the_expected_tags(self):
        expected = {
            "command.exec", "command.delete", "command.perms", "command.disk",
            "command.interpreter", "command.tool_exec", "proc.control",
            "net.egress", "pkg.install", "vcs.write", "file.write",
            "secret.read",
        }
        assert CONFIGURABLE_TAGS == expected

    def test_unknown_key_resolves_untagged(self):
        assert tag_for_pattern_key("nonsense") is ActionTag.UNTAGGED

    def test_detection_family_literals_resolve(self):
        assert tag_for_pattern_key("command parser limit exceeded") is ActionTag.PARSER_LIMIT
        assert tag_for_pattern_key("shell command via -c/-lc flag") is ActionTag.COMMAND_INTERPRETER

    def test_tool_exec_prefix_rule(self):
        key = "arbitrary program execution via sort --compress-program"
        assert tag_for_pattern_key(key) is ActionTag.COMMAND_TOOL_EXEC


# ---------------------------------------------------------------------------
# T2a — `hermes config` write-verb detection
# ---------------------------------------------------------------------------

class TestHermesConfigDetection:
    @pytest.fixture(autouse=True)
    def _import(self):
        import tools.approval as approval_module
        self._module = approval_module

    @pytest.mark.parametrize("cmd", [
        "hermes config set approvals.auto_approve legacy",
        "hermes config unset approvals.mode",
        "hermes config edit",
        "hermes --profile x config set approvals.mode smart",
        "hermes -p ade config set a b",
    ])
    def test_write_verbs_detected(self, cmd):
        detected, key, desc = self._module.detect_dangerous_command(cmd)
        assert detected, f"{cmd!r} should be detected"
        assert desc == "write hermes config via CLI (approval policy lives here)"
        assert tag_for_pattern_key(desc) is ActionTag.CONFIG_WRITE

    @pytest.mark.parametrize("cmd", [
        "hermes config show",
        "hermes config get approvals.mode",
        "hermes config path",
        "hermes config env-path",
        "hermes config check",
        "hermes config migrate",
    ])
    def test_read_and_migrate_verbs_not_detected(self, cmd):
        detected, _key, _desc = self._module.detect_dangerous_command(cmd)
        assert not detected, f"{cmd!r} must stay undetected (read/migrate verb)"

    def test_control_20_removal_breaks_detection(self):
        """Negative control 20: without the entry, no warning → approved."""
        with patch.object(self._module, "DANGEROUS_PATTERNS", [
            (p, d) for p, d in self._module.DANGEROUS_PATTERNS
            if d != "write hermes config via CLI (approval policy lives here)"
        ]):
            # recompile the compiled list exactly as module init does
            compiled = [(re, d) for re, d in self._module.DANGEROUS_PATTERNS_COMPILED
                        if d != "write hermes config via CLI (approval policy lives here)"]
            with patch.object(self._module, "DANGEROUS_PATTERNS_COMPILED", compiled):
                detected, _key, _desc = self._module.detect_dangerous_command(
                    "hermes config set approvals.mode off"
                )
                assert not detected


# ---------------------------------------------------------------------------
# T3/D14 — config-write override
# ---------------------------------------------------------------------------

class TestConfigWriteOverride:
    @pytest.fixture(autouse=True)
    def _import(self):
        import tools.approval as approval_module
        self._module = approval_module

    def _resolve(self, pattern_key, command, is_tirith=False):
        return self._module._resolve_tags(
            [(pattern_key, pattern_key, is_tirith)], command
        )

    def test_f1_tilde_form_becomes_config_write(self):
        # `recursive delete` would tag command.delete — override must win.
        tags = self._resolve(
            "recursive delete", "rm -rf ~/.hermes"
        )
        assert tags == frozenset({ActionTag.CONFIG_WRITE})

    def test_f2_env_var_form_becomes_config_write(self):
        tags = self._resolve(
            "recursive delete", "rm -rf $HERMES_HOME"
        )
        assert tags == frozenset({ActionTag.CONFIG_WRITE})

    def test_f3_interpreter_payload_becomes_config_write(self):
        # audit R3-2: interpreter payloads targeting the config file.
        tags = self._resolve(
            "shell command via -c/-lc flag",
            "bash -c 'echo x >> ~/.hermes/config.yaml'",
        )
        assert tags == frozenset({ActionTag.CONFIG_WRITE})

    def test_f4_bare_home_directory_becomes_config_write(self):
        # control 4: rm -rf ~/.hermes (the directory, not a filename).
        tags = self._resolve(
            "recursive delete", "rm -rf ~/.hermes"
        )
        assert tags == frozenset({ActionTag.CONFIG_WRITE})

    def test_f5_bare_hermes_home_no_trailing_slash(self):
        # control 19: $HERMES_HOME with no trailing slash must still match.
        tags = self._resolve(
            "recursive delete", "rm -rf $HERMES_HOME"
        )
        assert tags == frozenset({ActionTag.CONFIG_WRITE})

    def test_f6_cli_write_verb_untouched_by_override(self):
        # T2a tags statically; the override does not need to fire.
        tags = self._resolve(
            "write hermes config via CLI (approval policy lives here)",
            "hermes config set approvals.mode off",
        )
        assert tags == frozenset({ActionTag.CONFIG_WRITE})

    def test_tirith_never_overridden(self):
        tags = self._resolve(
            "tirith:rule-1", "rm -rf ~/.hermes", is_tirith=True
        )
        assert tags == frozenset({ActionTag.SECURITY_SCAN})

    def test_non_hermes_path_keeps_its_tag(self):
        tags = self._resolve(
            "recursive delete", "rm -rf /tmp/build"
        )
        assert tags == frozenset({ActionTag.COMMAND_DELETE})

    def test_control_3_precondition_removal_fails(self):
        """Negative control 3: restoring an 'only when file.write' precondition
        would let the interpreter payload through — assert the override is
        unconditional by checking the payload case is config.write."""
        tags = self._resolve(
            "shell command via -c/-lc flag",
            "bash -c 'echo x >> ~/.hermes/config.yaml'",
        )
        assert tags == frozenset({ActionTag.CONFIG_WRITE})

    def test_resolve_is_total(self):
        """G13: a malformed warnings entry must not raise — UNTAGGED fallback."""
        import tools.approval as approval_module
        tags = approval_module._resolve_tags([("x", "y", False)], "ok")
        assert tags == frozenset({ActionTag.UNTAGGED})
