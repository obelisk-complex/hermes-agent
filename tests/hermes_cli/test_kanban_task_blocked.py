import sqlite3

import hermes_cli.kanban_db as kdb


def _mk_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(kdb.SCHEMA_SQL)
    return conn


def test_record_task_failure_fires_blocked_hook(monkeypatch):
    calls = []
    monkeypatch.setattr(
        kdb, "_invoke_kanban_hook",
        lambda name, **kw: calls.append((name, kw)) or [],
    )
    conn = _mk_conn()
    now = 0
    conn.execute(
        "INSERT INTO tasks (id, title, status, created_at, "
        "consecutive_failures) VALUES (?,?,?,?,?)",
        ("t_blk", "x", "running", now, 0),
    )
    conn.commit()
    # max_retries unset → failure_limit=1 trips the breaker on the first call.
    blocked = kdb._record_task_failure(
        conn, "t_blk", "kaboom", outcome="crashed",
        failure_limit=1, release_claim=True, end_run=False,
    )
    assert blocked is True
    fired = [c for c in calls if c[0] == "fork_kanban_task_blocked"]
    assert len(fired) == 1
    kw = fired[0][1]
    assert kw["task_id"] == "t_blk"
    assert kw["trigger"] == "auto_block"
    assert kw["trigger_outcome"] == "crashed"
    assert kw["consecutive_failures"] == 1
    assert kw["effective_limit"] == 1
    assert kw["limit_source"] == "dispatcher"
    assert kw["reason"] == "kaboom"


def test_block_task_fires_manual_hook(monkeypatch):
    calls = []
    monkeypatch.setattr(
        kdb, "_invoke_kanban_hook",
        lambda name, **kw: calls.append((name, kw)) or [],
    )
    conn = _mk_conn()
    conn.execute(
        "INSERT INTO tasks (id, title, status, created_at) VALUES (?,?,?,?)",
        ("t_man", "x", "running", 0),
    )
    conn.commit()
    assert kdb.block_task(conn, "t_man", reason="manual stop") is True
    fired = [c for c in calls if c[0] == "fork_kanban_task_blocked"]
    assert len(fired) == 1
    assert fired[0][1]["trigger"] == "manual"
    assert fired[0][1]["reason"] == "manual stop"


def test_block_task_no_hook_when_noop(monkeypatch):
    calls = []
    monkeypatch.setattr(
        kdb, "_invoke_kanban_hook",
        lambda name, **kw: calls.append((name, kw)) or [],
    )
    conn = _mk_conn()
    conn.execute(
        "INSERT INTO tasks (id, title, status, created_at) VALUES (?,?,?,?)",
        ("t_done", "x", "done", 0),
    )
    conn.commit()
    # 'done' is not in ('running','ready') → rowcount 0 → returns False.
    assert kdb.block_task(conn, "t_done", reason="late") is False
    assert [c for c in calls if c[0] == "fork_kanban_task_blocked"] == []


def test_block_task_still_blocks_without_plugin():
    conn = _mk_conn()
    conn.execute(
        "INSERT INTO tasks (id, title, status, created_at) VALUES (?,?,?,?)",
        ("t_real", "x", "running", 0),
    )
    conn.commit()
    assert kdb.block_task(conn, "t_real", reason="r") is True
    row = conn.execute(
        "SELECT status FROM tasks WHERE id = ?", ("t_real",)
    ).fetchone()
    assert row["status"] == "blocked"


def test_blocked_hook_fires_outside_txn_callback_can_write(monkeypatch):
    """Proves the hook fires AFTER the write_txn commits: a callback that
    opens a write_txn from within the hook must succeed without a
    nested-transaction error. If the hook still fired inside write_txn this
    raises sqlite3.OperationalError ('cannot start a transaction within a
    transaction'). The load-bearing assertion is that write_txn succeeds."""
    conn = _mk_conn()
    conn.execute(
        "INSERT INTO tasks (id, title, status, created_at) VALUES (?,?,?,?)",
        ("t_cb", "x", "running", 0),
    )
    conn.commit()
    wrote = []

    def real_invoke(name, **kw):
        if name == "fork_kanban_task_blocked":
            # Open a write_txn from inside the callback - this proves the hook
            # fires after write_txn commits (not while the lock is held).
            with kdb.write_txn(conn):
                conn.execute(
                    "UPDATE tasks SET body = 'noted' WHERE id = ?",
                    (kw["task_id"],),
                )
            wrote.append(True)
        return []

    monkeypatch.setattr(kdb, "_invoke_kanban_hook", real_invoke)
    assert kdb.block_task(conn, "t_cb", reason="r") is True
    row = conn.execute(
        "SELECT status, body FROM tasks WHERE id = ?", ("t_cb",)
    ).fetchone()
    assert row["status"] == "blocked"   # txn committed first
    assert row["body"] == "noted"        # callback write succeeded
    assert wrote == [True]               # callback ran
