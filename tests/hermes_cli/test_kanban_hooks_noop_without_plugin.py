"""Backward-compat regression: kanban lifecycle hooks are no-ops with no plugin.

Pins the contract that when hook invocation contributes nothing, the three
new fire sites change nothing: spawn applies no override, block/auto-block
still block, and complete still completes. This is the safety net proving
those fire sites are inert plumbing, not a source of side effects on their
own.

NOTE (fork reality, see hermes_cli/plugins.py::FORK_MANDATORY_PLUGIN_KEYS):
quality-gate and self-check-enforcer are mandatory on this fork and cannot
be made absent via plugins.enabled/disabled — quality-gate specifically
registers callbacks on all three hooks these fire sites use
(pre_kanban_spawn, fork_kanban_task_blocked, pre_kanban_complete; see
plugins/quality-gate/__init__.py::register). "No plugin registered" is
therefore no longer a reachable state for these hook names, so the fire-site
tests below stub the invocation seam (``kdb._invoke_kanban_hook``) directly
rather than trying to force the plugin manager's hook registry empty —
poking ``PluginManager._hooks`` before invocation does NOT work here: the
manager's lazy discovery (``_delivery_manager``) re-runs on first
``invoke_hook`` call and repopulates it with the mandatory plugins'
callbacks, silently undoing the monkeypatch.
"""
import sqlite3

import hermes_cli.kanban_db as kdb


def _mk_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(kdb.SCHEMA_SQL)
    return conn


def _stub_kanban_hooks_empty(monkeypatch):
    """Force every kanban lifecycle hook fire site to see zero results.

    Patches the invocation seam kanban_db itself calls, not the plugin
    manager's internal registry: quality-gate is mandatory on this fork
    (FORK_MANDATORY_PLUGIN_KEYS) and its lazy discovery repopulates
    ``PluginManager._hooks`` on first use regardless of prior monkeypatching,
    so this is the only seam that reliably guarantees "hooks contributed
    nothing" for tests exercising the fire sites rather than the mechanism.
    """
    monkeypatch.setattr(kdb, "_invoke_kanban_hook", lambda *a, **k: [])


def test_invoke_hook_returns_empty_for_unregistered_hook_name():
    """Mechanism-level contract: a hook name nothing subscribes to -> [].

    Formerly asserted this using quality-gate's own hook names
    (pre_kanban_spawn / fork_kanban_task_blocked / pre_kanban_complete) under
    a forced-empty plugin registry, i.e. "with NO plugin registered". That
    premise is categorically false now: quality-gate is mandatory and
    registers callbacks on exactly those three names (see module docstring).
    This exercises the real, unstubbed plugin manager (mandatory plugins
    loaded, same as production) against a hook name no plugin — mandatory or
    otherwise — has ever registered, which still proves invoke_hook's "no
    subscriber -> []" plumbing without relying on an unreachable plugin-free
    state.
    """
    assert kdb._invoke_kanban_hook(
        "kanban_test_hook_with_no_subscribers", task_id="x"
    ) == []


def test_complete_task_unchanged_without_plugin(monkeypatch):
    _stub_kanban_hooks_empty(monkeypatch)
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
    _stub_kanban_hooks_empty(monkeypatch)
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
    _stub_kanban_hooks_empty(monkeypatch)
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
