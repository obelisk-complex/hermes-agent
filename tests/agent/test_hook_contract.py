"""Hook-kwarg contract guard (round-1 QA #16 / compat A5).

The self-check-enforcer plugin reads specific keyword arguments off each hook
invocation. Those kwarg NAMES are owned by upstream call sites. If an upstream
rebase renames one (e.g. `args=` → `function_args=`, or `tool_name=` → `name=`),
the plugin's handler silently stops scanning — the enforcement gate goes quiet
with NO error. Design note 16 records that exactly this happened once.

These tests assert each invoke site still passes the kwargs the plugin depends
on, so such a rename fails CI (and the pre-push sync gate) instead of silently
disabling the gate. They read source only (no imports, no side effects).

Runnable two ways:
    ./venv/bin/python3 tests/agent/test_hook_contract.py     # standalone
    python -m pytest tests/agent/test_hook_contract.py       # CI
"""
import os
import re

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))

# (relative file, hook name, kwargs the plugin reads off that hook)
_CONTRACTS = [
    ("hermes_cli/plugins.py",      "pre_tool_call",  ["tool_name", "args"]),
    ("model_tools.py",             "post_tool_call", ["tool_name", "result"]),
    ("tools/delegate_tool_results.py", "subagent_stop",
     ["parent_session_id", "child_session_id", "child_summary", "child_status"]),
    ("hermes_cli/plugins.py",      "pre_verify",     ["final_response", "session_id", "attempt"]),
]


def _invoke_window(src, hook):
    """Return the text of the (first) invoke_hook("<hook>", ...) call, or None.

    Matches the invoke call by any of its names — `invoke_hook(`, `_invoke_hook(`,
    or import aliases like `_on_invoke(` / `_budget_invoke(` (all contain
    "invoke") — allowing whitespace/newlines before the hook-name string. A
    600-char window reliably spans the one-kwarg-per-line argument list without
    bleeding into later code.
    """
    m = re.search(r'\w*invoke\w*\(\s*"' + re.escape(hook) + r'"', src)
    return src[m.start():m.start() + 600] if m else None


def _check_contract(relpath, hook, kwargs):
    with open(os.path.join(_ROOT, relpath), encoding="utf-8") as f:
        src = f.read()
    window = _invoke_window(src, hook)
    assert window is not None, f'no invoke_hook("{hook}", ...) call found in {relpath}'
    for kw in kwargs:
        assert re.search(rf"\b{kw}\s*=", window), (
            f'{relpath}: invoke_hook("{hook}", ...) no longer passes `{kw}=` — '
            f"the enforcer reads it; a rename here silently disables the gate"
        )


def test_pre_tool_call_contract():
    _check_contract(*_CONTRACTS[0])


def test_post_tool_call_contract():
    _check_contract(*_CONTRACTS[1])


def test_subagent_stop_contract():
    _check_contract(*_CONTRACTS[2])


def test_pre_verify_contract():
    _check_contract(*_CONTRACTS[3])


# The pre_verify kwargs above are upstream's own, so upstream renaming one would
# break upstream's callers too — that joint is far less fragile than the on_output
# one it replaces. The fragile joint MOVED: it is now the fork's widened
# precondition in agent/turn_stop_gates.py, a line inside a file upstream rewrites
# wholesale. If a sync restores upstream's `_edited`-only guard, the plugin stays
# loaded, its hook stays registered, and the gate simply never fires on the
# non-editing orchestrator turns it exists for. Nothing errors. Without the check
# below this migration would have no rebase tripwire at all.
def test_pre_verify_precondition_still_widened():
    with open(os.path.join(_ROOT, "agent/turn_stop_gates.py"), encoding="utf-8") as f:
        src = f.read()
    assert "api_call_count > 1" in src, (
        "agent/turn_stop_gates.py no longer widens the pre_verify precondition — "
        "the gate is back to firing only after file edits, so a subagent FAIL "
        "followed by a completion claim will pass unchallenged"
    )
    assert not re.search(r"if _edited and has_hook\(", src), (
        "agent/turn_stop_gates.py has upstream's `_edited`-only pre_verify guard back"
    )


def test_terminal_substitution_helper_present():
    """The other half: without it an exhausted budget ships the refused answer."""
    from agent.turn_stop_gates import pre_verify_terminal_substitute  # noqa: F401


# Both fork edits in agent/turn_final_response.py are single lines in a file
# upstream owns and rewrites wholesale, and neither is reachable from a
# behavioural test: test_pre_verify_gate.py calls apply_stop_gates directly and
# passes api_call_count itself, so a dropped call site is invisible to it.
# Deleting either line leaves the whole fork suite green. These two assertions
# are the only thing that fails when a sync reverts them.
def test_stop_gates_call_site_still_passes_api_call_count():
    with open(os.path.join(_ROOT, "agent/turn_final_response.py"), encoding="utf-8") as f:
        src = f.read()
    window = re.search(r"apply_stop_gates\(", src)
    assert window is not None, "agent/turn_final_response.py no longer calls apply_stop_gates"
    assert re.search(r"api_call_count\s*=\s*api_call_count",
                     src[window.start():window.start() + 600]), (
        "agent/turn_final_response.py stopped passing api_call_count= into "
        "apply_stop_gates — the widened precondition goes inert and the gate is "
        "back to firing only after file edits"
    )


def test_stop_gates_verdict_response_is_read_back():
    with open(os.path.join(_ROOT, "agent/turn_final_response.py"), encoding="utf-8") as f:
        src = f.read()
    assert re.search(r"final_response\s*=\s*_sg\.final_response", src), (
        "agent/turn_final_response.py no longer reads _sg.final_response — an "
        "exhausted nudge budget ships the very draft the gate refused"
    )


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
        except Exception as e:  # noqa: BLE001
            _failed += 1
            print(f"  ✗ {_t.__name__} — ERROR: {e!r}")
    print(f"\n=== {_passed} passed, {_failed} failed ===")
    sys.exit(1 if _failed else 0)
