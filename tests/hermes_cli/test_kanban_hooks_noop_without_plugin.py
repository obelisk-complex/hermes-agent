"""Backward-compat regression: kanban lifecycle hooks are no-ops with no plugin.

Pins the contract that with NO plugin registered, invoke_hook returns []
and the three new fire sites change nothing: spawn applies no override,
block/auto-block still block, and complete still completes. This is the
safety net proving the fork edits are invisible until a plugin opts in.
"""
import sqlite3

import hermes_cli.kanban_db as kdb
from hermes_cli.plugins import get_plugin_manager


def _mk_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(kdb.SCHEMA_SQL)
    return conn


def _no_plugins(monkeypatch):
    # Force a manager with an empty hook registry so invoke_hook -> [].
    mgr = get_plugin_manager()
    monkeypatch.setattr(mgr, "_hooks", {}, raising=False)


def test_invoke_hook_returns_empty_without_plugin(monkeypatch):
    _no_plugins(monkeypatch)
    assert kdb._invoke_kanban_hook("pre_kanban_spawn", task_id="x") == []
    assert kdb._invoke_kanban_hook("fork_kanban_task_blocked", task_id="x") == []
    assert kdb._invoke_kanban_hook("pre_kanban_complete", task_id="x") == []


def test_complete_task_unchanged_without_plugin(monkeypatch):
    _no_plugins(monkeypatch)
    conn = _mk_conn()
    conn.execute(
        "INSERT INTO tasks (id, title, status, created_at) VALUES (?,?,?,?)",
        ("t_done", "x", "running", 0),
    )
    conn.commit()
    assert kdb.complete_task(conn, "t_done", result="ok") is True
    assert conn.execute(
        "SELECT status FROM tasks WHERE id = ?", ("t_done",)
    ).fetchone()["status"] == "done"


def test_block_task_unchanged_without_plugin(monkeypatch):
    _no_plugins(monkeypatch)
    conn = _mk_conn()
    conn.execute(
        "INSERT INTO tasks (id, title, status, created_at) VALUES (?,?,?,?)",
        ("t_blk", "x", "running", 0),
    )
    conn.commit()
    assert kdb.block_task(conn, "t_blk", reason="r") is True
    assert conn.execute(
        "SELECT status FROM tasks WHERE id = ?", ("t_blk",)
    ).fetchone()["status"] == "blocked"


def test_auto_block_unchanged_without_plugin(monkeypatch):
    _no_plugins(monkeypatch)
    conn = _mk_conn()
    conn.execute(
        "INSERT INTO tasks (id, title, status, created_at, "
        "consecutive_failures) VALUES (?,?,?,?,?)",
        ("t_auto", "x", "running", 0, 0),
    )
    conn.commit()
    assert kdb._record_task_failure(
        conn, "t_auto", "boom", outcome="crashed",
        failure_limit=1, release_claim=True, end_run=False,
    ) is True
    assert conn.execute(
        "SELECT status FROM tasks WHERE id = ?", ("t_auto",)
    ).fetchone()["status"] == "blocked"
