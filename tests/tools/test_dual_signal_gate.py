"""Truth-table tests for evaluate_dual_signal (T5).

The rule order is load-bearing: legacy must be today's behaviour (verdict
alone), and every non-legacy row must fail closed. Mixed-configurable rows
(rule 7, all()) are unit-covered here; at the wired call sites a two-tag
prompt always contains security.scan, so rule 5 decides those before rule 7
(D6). Negative control 6: replacing all() with any() must fail these rows.
"""

import pytest

from tools.action_tags import ActionTag
from tools.auto_approval import evaluate_dual_signal

DEL = ActionTag.COMMAND_DELETE
PROC = ActionTag.PROC_CONTROL
UNTAGGED = ActionTag.UNTAGGED
CONFIG = ActionTag.CONFIG_WRITE
EXEC = ActionTag.CODE_EXEC


def _decide(**over):
    kwargs: dict = dict(
        tags=frozenset({DEL}),
        author_verdict=True,
        mode="legacy",
        enabled_tags=frozenset({"command.delete"}),
        manual_gate_open=False,
        enabled_by="config:/tmp/x/config.yaml",
    )
    kwargs.update(over)
    return evaluate_dual_signal(**kwargs)


class TestRule1NoAuthorVerdict:
    def test_denies_every_mode(self):
        for mode in ("legacy", "dual_signal", "off"):
            d = _decide(author_verdict=False, mode=mode)
            assert not d.auto_approved and d.reason == "no_author_verdict"


class TestRule2Off:
    def test_off_denies_with_verdict(self):
        d = _decide(mode="off")
        assert not d.auto_approved and d.reason == "off"

    def test_off_denies_even_when_tag_enabled(self):
        d = _decide(mode="off", enabled_tags=frozenset({"command.delete"}))
        assert not d.auto_approved


class TestRule3Legacy:
    def test_legacy_approves_with_verdict_alone(self):
        """Bit-identical to today: tags and barrier are not consulted."""
        d = _decide(mode="legacy", enabled_tags=frozenset())
        assert d.auto_approved and d.reason == "legacy"

    def test_legacy_approves_even_with_open_manual_gate(self):
        d = _decide(mode="legacy", manual_gate_open=True)
        assert d.auto_approved

    def test_legacy_approves_untagged(self):
        d = _decide(mode="legacy", tags=frozenset({UNTAGGED}))
        assert d.auto_approved


class TestRule4Untagged:
    def test_untagged_denied_in_dual_signal(self):
        d = _decide(mode="dual_signal", tags=frozenset({UNTAGGED}))
        assert not d.auto_approved and d.reason == "untagged"

    def test_empty_tags_denied_in_dual_signal(self):
        d = _decide(mode="dual_signal", tags=frozenset())
        assert not d.auto_approved and d.reason == "untagged"


class TestRule5NeverAutoApprovable:
    @pytest.mark.parametrize("tag", [CONFIG, EXEC, ActionTag.SECURITY_SCAN,
                                     ActionTag.PRIV_ESCALATE, ActionTag.PARSER_LIMIT])
    def test_denied_even_when_enabled(self, tag):
        d = _decide(mode="dual_signal", tags=frozenset({tag}),
                    enabled_tags=frozenset({tag.value}))
        assert not d.auto_approved and d.reason == "never_auto_approvable"

    def test_config_write_denied(self):
        d = _decide(mode="dual_signal", tags=frozenset({CONFIG}),
                    enabled_tags=frozenset({"config.write"}))
        assert not d.auto_approved and d.reason == "never_auto_approvable"


class TestRule6HeadOfLine:
    def test_open_gate_denies_dual_signal(self):
        d = _decide(mode="dual_signal", manual_gate_open=True)
        assert not d.auto_approved and d.reason == "head_of_line"

    def test_open_gate_denies_off(self):
        d = _decide(mode="off", manual_gate_open=False)
        assert not d.auto_approved and d.reason == "off"


class TestRule7AllTagsEnabled:
    def test_single_tag_enabled_approves(self):
        d = _decide(mode="dual_signal")
        assert d.auto_approved and d.reason == "dual_signal"

    def test_tag_not_enabled_denies(self):
        d = _decide(mode="dual_signal", enabled_tags=frozenset())
        assert not d.auto_approved and d.reason == "tag_not_enabled"

    def test_two_configurable_tags_all_enabled_approves(self):
        d = _decide(mode="dual_signal", tags=frozenset({DEL, PROC}),
                    enabled_tags=frozenset({"command.delete", "proc.control"}))
        assert d.auto_approved and d.reason == "dual_signal"

    def test_two_configurable_tags_one_enabled_denies(self):
        """Negative control 6: any() would approve this; all() must deny."""
        d = _decide(mode="dual_signal", tags=frozenset({DEL, PROC}),
                    enabled_tags=frozenset({"command.delete"}))
        assert not d.auto_approved and d.reason == "tag_not_enabled"

    def test_decision_carries_tags_and_attribution(self):
        d = _decide(mode="dual_signal", enabled_by="ops team")
        assert d.tags == ("command.delete",)
        assert d.enabled_by == "ops team"
