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
import symtable

_MAIN = os.path.join(os.path.dirname(__file__), "..", "..", "hermes_cli", "main.py")


def _main_source():
    with open(os.path.normpath(_MAIN), encoding="utf-8") as f:
        return f.read()


def test_critical_files_covers_customised_files():
    """#6: the three upstream-owned files the fork edits must be syntax-guarded."""
    src = _main_source()
    start = src.find("_UPDATE_CRITICAL_FILES = (")
    assert start != -1, "_UPDATE_CRITICAL_FILES tuple not found"
    end = src.find("\n)", start)  # closing paren on its own line
    assert end != -1, "_UPDATE_CRITICAL_FILES tuple not terminated"
    body = src[start:end]
    for f in ("agent/conversation_loop.py",
              "tools/delegate_tool.py",
              "hermes_cli/plugins.py"):
        assert f in body, (
            f"{f} is not in _UPDATE_CRITICAL_FILES — a conflict marker left by "
            f"a rebase would pass the post-pull syntax guard and brick the agent "
            f"at first turn instead of rolling back"
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
