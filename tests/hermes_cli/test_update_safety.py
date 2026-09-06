"""Safety guards for the customised `hermes update` path (round-1 QA #6/#7).

Both tests read hermes_cli/main.py as source (no import side effects) so they
are cheap and run in the CI `Tests` slices.

#6  The post-pull syntax guard (_validate_critical_files_syntax) only compiles
    files in _UPDATE_CRITICAL_FILES. The fork's customisation also edits
    agent/conversation_loop.py, tools/delegate_tool.py and hermes_cli/plugins.py;
    if a daily upstream rebase leaves a conflict marker in one of those, the
    guard never compiles it, the update is declared successful, and the agent
    crashes at the first turn instead of rolling back. They must be covered.

#7  A regression guard for the `subprocess` UnboundLocalError that actually
    shipped: a local `import subprocess` (ours or upstream's) anywhere inside
    the ~1400-line _cmd_update_impl retroactively makes `subprocess` a function
    local, turning every bare `subprocess.` reference above it into an
    UnboundLocalError at update time (py_compile does NOT catch it). Assert the
    common stdlib names stay module-global in that function.

Runnable two ways:
    ./venv/bin/python3 tests/hermes_cli/test_update_safety.py   # standalone
    python -m pytest tests/hermes_cli/test_update_safety.py     # CI
"""
import os

import pytest
import symtable

_MAIN = os.path.join(os.path.dirname(__file__), "..", "..", "hermes_cli", "update_cmd.py")


def _main_source():
    with open(os.path.normpath(_MAIN), encoding="utf-8") as f:
        return f.read()


def test_critical_files_covers_customised_files():
    """#6: every upstream-owned file the fork edits must be syntax-guarded.

    Derived, not hardcoded. The previous version named three specific files,
    and two of them stopped being true when upstream's decomposition moved the
    code the fork patches (the on_output gate now rides
    agent/turn_stop_gates.py, the child-prompt blocks
    tools/delegate_tool_progress.py) — leaving a guard that named files the
    fork no longer touches and missed the ones it does. Computing the set from
    the actual diff against upstream keeps this honest through the next move.
    """
    import subprocess

    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        out = subprocess.run(
            ["git", "diff", "--name-only", "upstream/main", "--", "*.py"],
            cwd=root, capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        pytest.skip("git unavailable; cannot derive the fork-edited file set")
    if out.returncode != 0:
        pytest.skip("no upstream/main ref in this checkout")
    edited = [
        f for f in out.stdout.split()
        if not f.startswith(("tests/", "plugins/", "skills/"))
    ]
    assert edited, "no fork-edited python files found — is upstream/main correct?"

    from hermes_cli.update_cmd import _UPDATE_CRITICAL_FILES

    unguarded = sorted(set(edited) - set(_UPDATE_CRITICAL_FILES))
    assert not unguarded, (
        f"fork-edited but not in _UPDATE_CRITICAL_FILES: {unguarded} — a "
        "conflict marker left by a rebase would pass the post-pull syntax "
        "guard and brick the agent at first turn instead of rolling back"
    )
    missing = [f for f in _UPDATE_CRITICAL_FILES
               if not os.path.exists(os.path.join(root, f))]
    assert not missing, (
        f"_UPDATE_CRITICAL_FILES names paths that do not exist: {missing} — "
        "a typo'd entry silently guards nothing"
    )


def test_cmd_update_impl_has_no_shadowed_stdlib_imports():
    """#7: stdlib names used bare in _cmd_update_impl must resolve to the module
    globals, never a function-local import (which causes UnboundLocalError)."""
    src = _main_source()
    top = symtable.symtable(src, "main.py", "exec")

    def _find(table):
        for child in table.get_children():
            if child.get_type() == "function" and child.get_name() == "_cmd_update_impl":
                return child
            found = _find(child)
            if found is not None:
                return found
        return None

    fn = _find(top)
    assert fn is not None, "_cmd_update_impl not found in hermes_cli/main.py"
    for name in ("subprocess", "os", "sys", "shutil"):
        try:
            sym = fn.lookup(name)
        except KeyError:
            continue  # name not referenced in this function — fine
        assert not sym.is_local(), (
            f"'{name}' is function-local in _cmd_update_impl — a shadowing "
            f"`import {name}` makes every bare `{name}.` reference above it raise "
            f"UnboundLocalError at `hermes update` time (py_compile won't catch it)"
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
