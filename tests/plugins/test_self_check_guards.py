"""Tests for the two advisory/guard hooks added to the self-check enforcer in
v3.7.2 (Claude-harness parity):

  * Chesterton's Fence — `transform_tool_result` on `write_file` / `patch`
    appends a "walk the history first" reminder (with recent `git log`) on the
    FIRST edit per (session, repo) inside a git repo. Never blocks.
  * Push/merge pre-flight — `pre_tool_call` on `terminal` BLOCKS once (then
    self-clears) before `git push` / `gh pr create|merge`, surfacing a
    pre-flight checklist so the model stops and confirms before an outward,
    hard-to-reverse action.

Stdlib + git only; real temp repos (no mocks). Runs standalone or under pytest.
"""
import importlib.util
import os
import subprocess

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PLUGIN = os.path.join(_REPO, "plugins", "self-check-enforcer", "__init__.py")


def _load():
    spec = importlib.util.spec_from_file_location("self_check_guards_under_test", _PLUGIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True, text=True, timeout=15,
    )


def _make_repo(tmp_path, subject="seed: initial commit"):
    repo = str(tmp_path)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.test")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "commit.gpgsign", "false")
    (tmp_path / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", subject)
    return repo


# ── Chesterton's Fence (transform_tool_result) ────────────────────────────

def test_chesterton_appends_history_reminder_on_first_edit(tmp_path):
    mod = _load()
    repo = _make_repo(tmp_path, subject="seed: distinctive-subject-xyz")
    out = mod.on_transform_tool_result(
        tool_name="write_file",
        args={"path": os.path.join(repo, "newfile.py")},
        result='{"status": "ok"}',
        session_id="s1",
    )
    assert isinstance(out, str), f"expected a decorated string, got {out!r}"
    assert '{"status": "ok"}' in out, "original result must be preserved"
    assert "Chesterton" in out, "reminder should name the principle"
    assert "distinctive-subject-xyz" in out, "recent git log should be embedded"


def test_chesterton_fires_once_per_repo_per_session(tmp_path):
    mod = _load()
    repo = _make_repo(tmp_path)
    args = {"path": os.path.join(repo, "a.py")}
    first = mod.on_transform_tool_result(
        tool_name="write_file", args=args, result="R", session_id="s1")
    second = mod.on_transform_tool_result(
        tool_name="patch", args={"path": os.path.join(repo, "b.py")},
        result="R", session_id="s1")
    assert isinstance(first, str), "first edit in the repo should fire"
    assert second is None, "second edit in same (session, repo) must NOT re-fire"


def test_chesterton_silent_outside_git_repo(tmp_path):
    mod = _load()
    out = mod.on_transform_tool_result(
        tool_name="write_file",
        args={"path": os.path.join(str(tmp_path), "loose.py")},
        result="R", session_id="s1",
    )
    assert out is None, "no git repo -> no reminder"


def test_chesterton_skips_error_results(tmp_path):
    mod = _load()
    repo = _make_repo(tmp_path)
    out = mod.on_transform_tool_result(
        tool_name="write_file",
        args={"path": os.path.join(repo, "x.py")},
        result='{"error": "disk full"}',
        session_id="s1",
    )
    assert out is None, "a failed edit should not be decorated"


def test_delegate_task_fail_annotation_still_works(tmp_path):
    """Regression: extending the handler must not break the delegate_task path."""
    mod = _load()
    out = mod.on_transform_tool_result(
        tool_name="delegate_task",
        args={},
        result="subagent says FAIL: auth bypass",
        session_id="s1",
    )
    assert isinstance(out, str) and "GATE CHECK" in out, \
        "delegate_task FAIL annotation must be preserved"


# ── Push/merge pre-flight (pre_tool_call, block-once) ─────────────────────

def test_push_preflight_blocks_first_git_push():
    mod = _load()
    out = mod.on_pre_tool_call(
        tool_name="terminal",
        args={"command": "git push origin main"},
        session_id="s1",
    )
    assert isinstance(out, dict) and out.get("action") == "block", \
        f"first git push should be blocked, got {out!r}"
    msg = out["message"].lower()
    assert "pre-flight" in msg, "block message should present a pre-flight checklist"
    assert "re-run" in msg, "block message should tell the model how to proceed"


def test_push_preflight_allows_second_identical_push():
    mod = _load()
    cmd = {"command": "git push origin main"}
    first = mod.on_pre_tool_call(tool_name="terminal", args=cmd, session_id="s1")
    second = mod.on_pre_tool_call(tool_name="terminal", args=cmd, session_id="s1")
    assert isinstance(first, dict) and first.get("action") == "block"
    assert second is None, "re-running the SAME push must be allowed (self-clearing)"


def test_push_preflight_matches_gh_pr_create():
    mod = _load()
    out = mod.on_pre_tool_call(
        tool_name="terminal",
        args={"command": "gh pr create --fill"},
        session_id="s1",
    )
    assert isinstance(out, dict) and out.get("action") == "block", \
        "gh pr create should trip the pre-flight"


def test_push_preflight_ignores_non_push_terminal():
    mod = _load()
    out = mod.on_pre_tool_call(
        tool_name="terminal",
        args={"command": "ls -la && git status"},
        session_id="s1",
    )
    assert out is None, "ordinary terminal commands must pass through untouched"


if __name__ == "__main__":
    import sys
    import tempfile
    import pathlib

    _tests = [(k, v) for k, v in sorted(globals().items())
              if k.startswith("test_") and callable(v)]
    _p = _f = 0
    for _name, _t in _tests:
        try:
            if "tmp_path" in _t.__code__.co_varnames[: _t.__code__.co_argcount]:
                with tempfile.TemporaryDirectory() as _d:
                    _t(pathlib.Path(_d))
            else:
                _t()
            _p += 1
            print(f"  ✓ {_name}")
        except AssertionError as e:
            _f += 1
            print(f"  ✗ {_name} — {e}")
    print(f"\n=== {_p} passed, {_f} failed ===")
    sys.exit(1 if _f else 0)
