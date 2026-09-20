"""Guards the mandatory instructions injected into delegate_task child prompts.

These instructions are load-bearing for the self-check enforcement gate (NO-OP
rejection, VERIFIES_TASK echo), the v3.7.0 acceptance-scenarios requirement and
the v3.7.1 verdict mandate. A rebase that drops one silently weakens the gate.

This asserts against the prompt `_build_child_system_prompt` actually BUILDS,
not against the text of the file that defines it. The previous version grepped
`tools/delegate_tool.py` for the literal strings, which had two failure modes it
could not see: upstream's September 2026 decomposition moved the function to
`tools/delegate_tool_progress.py` (the grep would have read a file that no
longer contains the prompt at all), and a hoisted-to-constant refactor can leave
every string present in the source while nothing appends it to `parts`. Building
the prompt covers both. Every guarantee the source-grep version made is still
asserted here.

Anti-fabrication is deliberately NOT asserted: the child prompt is passed as
`ephemeral_system_prompt`, which is appended to the built system prompt, so
upstream's TASK_COMPLETION_GUIDANCE already reaches every child. The fork does
not restate it.

Runnable two ways:
    ./venv/bin/python3 tests/tools/test_delegate_instructions.py   # standalone
    python -m pytest tests/tools/test_delegate_instructions.py     # CI
"""

from tools.delegate_tool_progress import _build_child_system_prompt


def _prompt(role="leaf"):
    return _build_child_system_prompt("do the thing", role=role)


def test_noop_rejection_instruction_present():
    assert "NO-OP REJECTION" in _prompt(), "NO-OP rejection guard instruction missing"


def test_verifies_task_instruction_present():
    assert "VERIFIES_TASK INSTRUCTION" in _prompt(), "verifies_task echo instruction missing"


def test_acceptance_scenarios_instruction_present():
    """v3.7.0: the child must RUN each listed acceptance scenario and report its
    outcome (command + exit code) before claiming completion."""
    p = _prompt()
    assert "ACCEPTANCE SCENARIOS" in p, "acceptance-scenarios instruction missing"
    assert "exit code" in p.lower(), \
        "acceptance-scenarios instruction must require reporting command + exit code"


def test_verdict_mandate_instruction_present():
    """v3.7.1: the child must emit an explicit verdict (READY/NEEDS_WORK/BLOCKED)
    in the delegate prompt itself, not only via the skill -- so verdict emission
    (and the NEEDS_WORK gate that depends on it) does not hinge on the child
    having loaded self-checking-harness."""
    p = _prompt()
    assert "VERDICT INSTRUCTION" in p, "verdict mandate instruction missing"
    assert "NEEDS_WORK" in p and "BLOCKED" in p and "READY" in p, \
        "verdict instruction must enumerate READY/NEEDS_WORK/BLOCKED"


def test_verdict_tokens_match_self_check_enforcer_regexes():
    """The enforcer parses the child's verdict line; a vocabulary change here
    (e.g. aligning to upstream's goal-judge DONE/BLOCKED/WAIT) would silently
    stop the re-runnable NEEDS_WORK gate from ever firing."""
    import re

    p = _prompt()
    needs_work = re.compile(r'\bverdict\b["\']?\s*[:=]\s*["\']?NEEDS_WORK\b', re.IGNORECASE)
    blocked = re.compile(r'\bverdict\b["\']?\s*[:=]\s*["\']?BLOCKED\b', re.IGNORECASE)
    assert needs_work.search(p), "prompt must show a `verdict: NEEDS_WORK` example the enforcer can match"
    assert blocked.search(p), "prompt must show a `verdict: BLOCKED` example the enforcer can match"


def test_instructions_survive_orchestrator_role():
    """The orchestrator branch appends more text; the mandatory blocks must
    still be present, not replaced."""
    p = _prompt(role="orchestrator")
    for token in ("NO-OP REJECTION", "VERIFIES_TASK INSTRUCTION",
                  "ACCEPTANCE SCENARIOS", "VERDICT INSTRUCTION"):
        assert token in p, f"{token} missing from orchestrator-role prompt"


if __name__ == "__main__":
    import sys
    _tests = [v for k, v in sorted(globals().items())
              if k.startswith("test_") and callable(v)]
    _passed = _failed = 0
    for _t in _tests:
        try:
            _t()
            _passed += 1
            print(f"  ✓ {_t.__name__}")
        except AssertionError as e:
            _failed += 1
            print(f"  ✗ {_t.__name__} — {e}")
    print(f"\n=== {_passed} passed, {_failed} failed ===")
    sys.exit(1 if _failed else 0)
