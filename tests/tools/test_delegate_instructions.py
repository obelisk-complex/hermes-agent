"""Guards the mandatory instructions injected into delegate_task child prompts.

These instructions live in the upstream-owned `_build_child_system_prompt` and
are load-bearing for the self-check enforcement gate (NO-OP rejection,
VERIFIES_TASK echo) plus the v3.7.0 acceptance-scenarios requirement. A rebase
that drops one silently weakens the gate, so assert they survive (source read
only — no imports / side effects).

Runnable two ways:
    ./venv/bin/python3 tests/tools/test_delegate_instructions.py   # standalone
    python -m pytest tests/tools/test_delegate_instructions.py     # CI
"""
import os

_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "tools", "delegate_tool.py")


def _src():
    with open(os.path.normpath(_SRC), encoding="utf-8") as f:
        return f.read()


def test_noop_rejection_instruction_present():
    assert "NO-OP REJECTION" in _src(), "NO-OP rejection guard instruction missing"


def test_verifies_task_instruction_present():
    assert "VERIFIES_TASK INSTRUCTION" in _src(), "verifies_task echo instruction missing"


def test_acceptance_scenarios_instruction_present():
    """v3.7.0: the child must RUN each listed acceptance scenario and report its
    outcome (command + exit code) before claiming completion."""
    s = _src()
    assert "ACCEPTANCE SCENARIOS" in s, "acceptance-scenarios instruction missing"
    assert "exit code" in s.lower(), \
        "acceptance-scenarios instruction must require reporting command + exit code"


def test_verdict_mandate_instruction_present():
    """v3.7.1: the child must emit an explicit verdict (READY/NEEDS_WORK/BLOCKED)
    in the delegate prompt itself, not only via the skill -- so verdict emission
    (and the NEEDS_WORK gate that depends on it) does not hinge on the child
    having loaded self-checking-harness."""
    s = _src()
    assert "VERDICT INSTRUCTION" in s, "verdict mandate instruction missing"
    assert "NEEDS_WORK" in s and "BLOCKED" in s and "READY" in s, \
        "verdict instruction must enumerate READY/NEEDS_WORK/BLOCKED"


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
