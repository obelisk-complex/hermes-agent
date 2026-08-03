import subprocess

import pytest

import githygiene


def _git(ws, *args):
    subprocess.run(["git", "-C", str(ws), *args], check=True,
                   capture_output=True, text=True)


def _has_git():
    try:
        subprocess.run(["git", "--version"], check=True, capture_output=True)
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _has_git(), reason="git not installed")


def test_non_repo_is_treated_clean(tmp_workspace):
    r = githygiene.check_hygiene(tmp_workspace)
    assert r.is_repo is False
    assert r.clean is True


def test_clean_repo(tmp_workspace):
    _git(tmp_workspace, "init", "-q")
    _git(tmp_workspace, "config", "user.email", "t@t")
    _git(tmp_workspace, "config", "user.name", "t")
    (tmp_workspace / "a.txt").write_text("x", encoding="utf-8")
    _git(tmp_workspace, "add", "a.txt")
    _git(tmp_workspace, "commit", "-q", "-m", "init")
    r = githygiene.check_hygiene(tmp_workspace)
    assert r.is_repo is True
    assert r.clean is True
    assert r.dirty_paths == []


def test_dirty_repo_lists_paths(tmp_workspace):
    _git(tmp_workspace, "init", "-q")
    (tmp_workspace / "untracked.txt").write_text("x", encoding="utf-8")
    r = githygiene.check_hygiene(tmp_workspace)
    assert r.is_repo is True
    assert r.clean is False
    assert any("untracked.txt" in p for p in r.dirty_paths)
