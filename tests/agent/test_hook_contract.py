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
    ("tools/delegate_tool.py",     "subagent_stop",
     ["parent_session_id", "child_session_id", "child_summary", "child_status"]),
    ("agent/conversation_loop.py", "on_output",      ["response_text", "session_id"]),
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


def test_on_output_contract():
    _check_contract(*_CONTRACTS[3])


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
