"""Stdlib-only guard for the fork-local kanban lifecycle hooks (B2).

Runs in the upstream-sync pre-push gate as `python3 tests/test_b2_kanban_hooks.py`
with only the repo root on sys.path - so it imports NOTHING from the repo and
reads source text instead (mirrors tests/agent/test_hook_contract.py). It
fails the sync if a rebase drops a hook name, renames a fire-site kwarg, or
removes the pre_kanban_complete block loop.

Runnable two ways:
    python3 tests/test_b2_kanban_hooks.py            # standalone (CI gate)
    python -m pytest tests/test_b2_kanban_hooks.py   # local
"""
import os
import re

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))

_HOOK_NAMES = ("pre_kanban_spawn", "kanban_task_blocked", "pre_kanban_complete")


def _read(relpath):
    with open(os.path.join(_ROOT, relpath), encoding="utf-8") as f:
        return f.read()


def _valid_hooks_literal(src):
    """Return the text of the VALID_HOOKS set literal."""
    m = re.search(r"VALID_HOOKS\s*:\s*Set\[str\]\s*=\s*\{", src)
    assert m, "VALID_HOOKS literal not found in plugins.py"
    start = m.end() - 1  # the '{'
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError("unterminated VALID_HOOKS literal")


def _function_body(src, func_name):
    """Return the body text of the named top-level function.

    Scans from the 'def <func_name>' line to the next 'def ' or 'class '
    at the same-or-shallower indentation (column 0), so nested defs and
    inner classes are included in the body text. Returns None if the
    function is not found.

    This is used to scope kwarg assertions to the correct function, avoiding
    false positives from unrelated code and false negatives from call-site
    windows that are too narrow.
    """
    pattern = re.compile(r'^def ' + re.escape(func_name) + r'\b', re.MULTILINE)
    m = pattern.search(src)
    if m is None:
        return None
    start = m.start()
    # Find the next top-level def/class after the start
    next_top = re.compile(r'\n(?=def |class )', re.MULTILINE)
    end_m = next_top.search(src, start + 1)
    if end_m:
        return src[start:end_m.start()]
    return src[start:]


def _invoke_kanban_hook_call_sites(src, hook):
    """Return a list of (start, end) positions for every
    _invoke_kanban_hook("<hook>", ...) call in src.

    Used to assert the NUMBER of fire sites for a hook.
    """
    pattern = re.compile(
        r'_invoke_kanban_hook\(\s*"' + re.escape(hook) + r'"'
    )
    return [m.start() for m in pattern.finditer(src)]


# ---------------------------------------------------------------------------
# 1) Hook names registered
# ---------------------------------------------------------------------------
def test_hooks_in_valid_hooks():
    literal = _valid_hooks_literal(_read("hermes_cli/plugins.py"))
    for name in _HOOK_NAMES:
        assert f'"{name}"' in literal, f"{name} missing from VALID_HOOKS"


# ---------------------------------------------------------------------------
# 1b) TEETH: prove the guard goes RED on a hookless source
# ---------------------------------------------------------------------------
def test_guard_has_teeth_on_hookless_source():
    """Negative-sensitivity check: feed the SAME extraction + membership
    logic a synthetic VALID_HOOKS literal that omits the three names and
    assert it would fail. Without this, a broken regex (one that never
    matches) could silently pass on a repo where the hooks were never added.
    Runs at the current ordering (after Tasks 1-4) and still proves teeth."""
    hookless = (
        'VALID_HOOKS: Set[str] = {\n'
        '    "pre_tool_call",\n'
        '    "post_approval_response",\n'
        '}\n'
    )
    literal = _valid_hooks_literal(hookless)
    missing = [n for n in _HOOK_NAMES if f'"{n}"' not in literal]
    assert missing == list(_HOOK_NAMES), (
        "guard failed to detect missing hooks in a hookless source - "
        "its extraction/membership logic has no teeth"
    )

    # Prove _function_body returns None on a source that lacks the function.
    assert _function_body("def other():\n    return None\n",
                          "pre_kanban_spawn") is None, (
        "function-body extractor should return None for a missing function"
    )

    # Prove the fire-site counter finds nothing in a hookless source.
    assert _invoke_kanban_hook_call_sites(
        "def f():\n    return None\n", "kanban_task_blocked"
    ) == [], (
        "fire-site scanner should find nothing in a hookless source"
    )

    # Prove that a hookless kanban_db would fail the pre_kanban_spawn check.
    hookless_db = (
        "def _apply_pre_kanban_spawn_override(claimed, *, board):\n"
        "    pass\n"
    )
    body = _function_body(hookless_db, "_apply_pre_kanban_spawn_override")
    assert body is not None
    missing_kwargs = [
        kw for kw in ("task_id", "title", "board")
        if not re.search(rf"\b{kw}\s*=", body)
    ]
    assert missing_kwargs == ["task_id", "title", "board"], (
        "kwarg check should detect all missing kwargs in hookless spawn body"
    )

    # Prove that a hookless kanban_db would fail the kanban_task_blocked check.
    hookless_db2 = "def block_task(conn, task_id):\n    return True\n"
    sites = _invoke_kanban_hook_call_sites(hookless_db2, "kanban_task_blocked")
    assert len(sites) == 0, (
        "fire-site count should be 0 in source with no kanban_task_blocked calls"
    )


# ---------------------------------------------------------------------------
# 2) pre_kanban_spawn: all documented kwargs present in the function body
#    that fires the hook (_apply_pre_kanban_spawn_override)
# ---------------------------------------------------------------------------
_PRE_SPAWN_KWARGS = [
    "task_id", "title", "body", "assignee", "model_override",
    "workspace_path", "workspace_kind", "branch_name", "priority",
    "skills", "consecutive_failures", "board",
]


def test_pre_kanban_spawn_kwargs_in_function_body():
    src = _read("hermes_cli/kanban_db.py")
    body = _function_body(src, "_apply_pre_kanban_spawn_override")
    assert body is not None, (
        "_apply_pre_kanban_spawn_override not found in kanban_db.py"
    )
    assert '_invoke_kanban_hook("pre_kanban_spawn"' in body or \
           "_invoke_kanban_hook(\n        \"pre_kanban_spawn\"" in body or \
           "_invoke_kanban_hook(\n    \"pre_kanban_spawn\"" in body, (
        '_invoke_kanban_hook("pre_kanban_spawn", ...) '
        "not found in _apply_pre_kanban_spawn_override body"
    )
    # Verify the hook is actually called (handles multi-line call form too)
    assert re.search(r'_invoke_kanban_hook\s*\(\s*["\']pre_kanban_spawn["\']', body), (
        '_invoke_kanban_hook("pre_kanban_spawn") '
        "call not found in _apply_pre_kanban_spawn_override body"
    )
    for kw in _PRE_SPAWN_KWARGS:
        assert re.search(rf"\b{kw}\s*=", body), (
            f"_apply_pre_kanban_spawn_override no longer passes `{kw}=` "
            f"to _invoke_kanban_hook (or it was renamed/removed)"
        )


# ---------------------------------------------------------------------------
# 3) kanban_task_blocked: both fire sites present + dict definitions contain
#    documented keys + both trigger literals present in source
# ---------------------------------------------------------------------------
_BLOCKED_REQUIRED_KEYS = ("task_id", "reason", "trigger")


def test_kanban_task_blocked_has_both_fire_sites():
    """Two fire sites: auto-block (_record_task_failure) + manual (block_task).
    The hook is fired via **_blocked_hook_kwargs (a captured dict), so we
    assert the NUMBER of call sites and the presence of the dict definitions
    rather than scanning a narrow window around each call.
    """
    src = _read("hermes_cli/kanban_db.py")
    sites = _invoke_kanban_hook_call_sites(src, "kanban_task_blocked")
    assert len(sites) >= 2, (
        "expected kanban_task_blocked fired from BOTH _record_task_failure "
        f"and block_task; found {len(sites)} fire site(s)"
    )
    # Both fire sites use **_blocked_hook_kwargs - assert the splat pattern
    # exists in both enclosing functions.
    block_task_body = _function_body(src, "block_task")
    record_failure_body = _function_body(src, "_record_task_failure")
    assert block_task_body is not None, "block_task function not found"
    assert record_failure_body is not None, "_record_task_failure function not found"
    assert "_invoke_kanban_hook(\"kanban_task_blocked\", **_blocked_hook_kwargs)" \
        in block_task_body, (
        "kanban_task_blocked call with **_blocked_hook_kwargs missing from block_task"
    )
    assert "_invoke_kanban_hook(\"kanban_task_blocked\", **_blocked_hook_kwargs)" \
        in record_failure_body, (
        "kanban_task_blocked call with **_blocked_hook_kwargs missing "
        "from _record_task_failure"
    )
    # Confirm the dict definitions contain the documented keys.
    for func_name, body in [("block_task", block_task_body),
                             ("_record_task_failure", record_failure_body)]:
        # Locate the _blocked_hook_kwargs = dict( ... ) definition in this body.
        assert re.search(r'_blocked_hook_kwargs\s*=\s*dict\s*\(', body), (
            f"_blocked_hook_kwargs = dict(...) not found in {func_name}"
        )
        for key in _BLOCKED_REQUIRED_KEYS:
            assert re.search(rf"\b{key}\s*=", body), (
                f"{func_name}: _blocked_hook_kwargs dict missing key `{key}=`"
            )
    # Assert both trigger literals appear in their respective function bodies.
    assert re.search(r'trigger\s*=\s*["\']auto_block["\']', record_failure_body), (
        'trigger="auto_block" literal missing from _record_task_failure body'
    )
    assert re.search(r'trigger\s*=\s*["\']manual["\']', block_task_body), (
        'trigger="manual" literal missing from block_task body'
    )


# ---------------------------------------------------------------------------
# 4) pre_kanban_complete block loop present + semantics proven
# ---------------------------------------------------------------------------
def test_complete_has_block_loop():
    src = _read("hermes_cli/kanban_db.py")
    # The block loop must inspect dict results for action == "block" and
    # raise CompletionBlockedError. Pin the load-bearing tokens.
    assert 'pre_kanban_complete' in src
    assert 'CompletionBlockedError' in src
    assert re.search(r'\.get\(\s*"action"\s*\)\s*!=\s*"block"', src), (
        "pre_kanban_complete block check (action != 'block') missing"
    )


def test_block_semantics_contract():
    """Self-contained proof of the invoke+block contract the real code uses.

    Mirrors PluginManager.invoke_hook (first non-None results) and the
    complete_task block loop (first {action:block,message:str} wins, aborts).
    No repo imports - pure stdlib - so it runs in the CI sync gate.
    """
    class _Blocked(Exception):
        pass

    def invoke(callbacks, **kwargs):
        out = []
        for cb in callbacks:
            try:
                r = cb(**kwargs)
            except Exception:
                continue  # isolated, like the real manager
            if r is not None:
                out.append(r)
        return out

    def complete(callbacks, *, task_id, result):
        # status starts 'running'; only flips to 'done' if no block.
        status = {"v": "running"}
        for r in invoke(callbacks, task_id=task_id, result=result):
            if isinstance(r, dict) and r.get("action") == "block":
                msg = r.get("message")
                if isinstance(msg, str) and msg:
                    raise _Blocked(msg)
        status["v"] = "done"
        return status["v"]

    # No block -> completes.
    assert complete([lambda **kw: None], task_id="t", result="ok") == "done"
    # Block -> aborts (raises, never reaches 'done').
    blocked = [lambda **kw: {"action": "block", "message": "tests failing"}]
    raised = False
    try:
        complete(blocked, task_id="t", result="ok")
    except _Blocked as e:
        raised = True
        assert str(e) == "tests failing"
    assert raised, "a block directive must abort completion"
    # First valid block wins over a later one.
    two = [
        lambda **kw: {"action": "block", "message": "first"},
        lambda **kw: {"action": "block", "message": "second"},
    ]
    try:
        complete(two, task_id="t", result="ok")
    except _Blocked as e:
        assert str(e) == "first"


if __name__ == "__main__":
    import sys
    _tests = [v for k, v in sorted(globals().items())
              if k.startswith("test_") and callable(v)]
    _passed = _failed = 0
    for _t in _tests:
        try:
            _t()
            _passed += 1
            print(f"  PASS {_t.__name__}")
        except AssertionError as e:
            _failed += 1
            print(f"  FAIL {_t.__name__} -- {e}")
        except Exception as e:  # noqa: BLE001
            _failed += 1
            print(f"  ERROR {_t.__name__} -- ERROR: {e!r}")
    print(f"\n=== {_passed} passed, {_failed} failed ===")
    sys.exit(1 if _failed else 0)
