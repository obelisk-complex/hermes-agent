"""Dual-signal auto-approval decision (T5 of the dual-signal plan).

Pure function, no I/O, no config reads: every input is a parameter, so the
truth table is exhaustively testable. The rule order is load-bearing — it is
ordered so ``legacy`` is provably today's behaviour (guardian verdict alone):

    1. ``not author_verdict``          → no  ("no_author_verdict")
    2. ``mode == "off"``               → no  ("off")
    3. ``mode == "legacy"``            → yes ("legacy")  — tags and barrier not consulted
    4. ``not tags or UNTAGGED in tags``→ no  ("untagged")
    5. any tag not in CONFIGURABLE_TAGS→ no  ("never_auto_approvable")
    6. ``manual_gate_open``            → no  ("head_of_line")
    7. all tags in enabled_tags        → yes ("dual_signal"), else no ("tag_not_enabled")

Call sites pass real values; this function alone decides (D6). The mixed-tag
rows of rule 7 are unit-level coverage — at both wired call sites a two-tag
prompt always contains ``security.scan`` (never-auto-approvable), so rule 5
decides those before rule 7 is reached (D6 of the plan).
"""

from dataclasses import dataclass

from tools.action_tags import ActionTag, CONFIGURABLE_TAGS

__all__ = ["evaluate_dual_signal", "AutoApprovalDecision"]


@dataclass(frozen=True)
class AutoApprovalDecision:
    auto_approved: bool
    reason: str
    tags: tuple[str, ...]
    enabled_by: str


def evaluate_dual_signal(
    *,
    tags: frozenset[ActionTag],
    author_verdict: bool,
    mode: str,
    enabled_tags: frozenset[str],
    manual_gate_open: bool,
    enabled_by: str = "",
) -> AutoApprovalDecision:
    """Decide whether an action may auto-approve under the dual-signal rule."""
    tag_values = tuple(sorted(t.value for t in tags))

    if not author_verdict:
        return AutoApprovalDecision(False, "no_author_verdict", tag_values, enabled_by)
    if mode == "off":
        return AutoApprovalDecision(False, "off", tag_values, enabled_by)
    if mode == "legacy":
        # Today's behaviour, bit-identical by construction: the guardian
        # verdict alone auto-approves. Tags and barrier are not consulted.
        return AutoApprovalDecision(True, "legacy", tag_values, enabled_by)
    if not tags or ActionTag.UNTAGGED in tags:
        return AutoApprovalDecision(False, "untagged", tag_values, enabled_by)
    if any(t.value not in CONFIGURABLE_TAGS for t in tags):
        return AutoApprovalDecision(False, "never_auto_approvable", tag_values, enabled_by)
    if manual_gate_open:
        return AutoApprovalDecision(False, "head_of_line", tag_values, enabled_by)
    if all(t.value in enabled_tags for t in tags):
        return AutoApprovalDecision(True, "dual_signal", tag_values, enabled_by)
    return AutoApprovalDecision(False, "tag_not_enabled", tag_values, enabled_by)
