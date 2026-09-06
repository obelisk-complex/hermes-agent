import sqlite3

import pytest

import hermes_cli.kanban_db as kdb


def _mk_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(kdb.SCHEMA_SQL)
    return conn


def _insert_running(conn, tid="t_c"):
    conn.execute(
        "INSERT INTO tasks (id, title, status, created_at, assignee, "
        "workspace_path, branch_name, model_override) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (tid, "x", "running", 0, "worker", "/tmp/ws", "wt/x", "m-default"),
    )
    conn.commit()


def test_pre_kanban_complete_fires_with_kwargs(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        kdb, "_invoke_kanban_hook",
        lambda name, **kw: seen.update({name: kw}) or [],
    )
    conn = _mk_conn()
    _insert_running(conn)
    assert kdb.complete_task(conn, "t_c", result="ok") is True
    kw = seen["pre_kanban_complete"]
    assert kw["task_id"] == "t_c"
    assert kw["result"] == "ok"
    assert kw["workspace_path"] == "/tmp/ws"
    assert kw["branch_name"] == "wt/x"
    assert kw["assignee"] == "worker"
    assert kw["model_override"] == "m-default"
    # First attempt → no prior completion_blocked_plugin events.
    assert kw["blocked_attempt_count"] == 0


def test_block_dict_aborts_and_task_keeps_running(monkeypatch):
    """Integration-style proof the gate's interception works: a returned
    block dict aborts complete_task (raises) and leaves the task running.
    This is the load-bearing assertion that complete_task actually FIRES the
    hook and scans results for the first {action:block,message}."""
    fired = {}

    def gate(name, **kw):
        fired[name] = kw
        if name == "pre_kanban_complete":
            return [{"action": "block", "message": "gate says no"}]
        return []

    monkeypatch.setattr(kdb, "_invoke_kanban_hook", gate)
    conn = _mk_conn()
    _insert_running(conn)
    with pytest.raises(kdb.CompletionBlockedError):
        kdb.complete_task(conn, "t_c", result="ok")
    # Hook WAS fired (interception point proven) and task is still running.
    assert "pre_kanban_complete" in fired
    row = conn.execute(
        "SELECT status FROM tasks WHERE id = ?", ("t_c",)
    ).fetchone()
    assert row["status"] == "running"


def test_blocked_attempt_count_increments_across_blocks(monkeypatch):
    """blocked_attempt_count reflects prior completion_blocked_plugin events,
    so a gate plugin can implement bounded retry / escalation."""
    counts = []

    def gate(name, **kw):
        if name == "pre_kanban_complete":
            counts.append(kw["blocked_attempt_count"])
            return [{"action": "block", "message": "still failing"}]
        return []

    monkeypatch.setattr(kdb, "_invoke_kanban_hook", gate)
    conn = _mk_conn()
    _insert_running(conn)
    for _ in range(3):
        with pytest.raises(kdb.CompletionBlockedError):
            kdb.complete_task(conn, "t_c", result="ok")
    assert counts == [0, 1, 2]


def test_pre_kanban_complete_block_aborts(monkeypatch):
    monkeypatch.setattr(
        kdb, "_invoke_kanban_hook",
        lambda name, **kw: [{"action": "block", "message": "tests failing"}],
    )
    conn = _mk_conn()
    _insert_running(conn)
    with pytest.raises(kdb.CompletionBlockedError) as ei:
        kdb.complete_task(conn, "t_c", result="ok")
    assert "tests failing" in str(ei.value)
    # State must be untouched - NOT done.
    row = conn.execute(
        "SELECT status, completed_at FROM tasks WHERE id = ?", ("t_c",)
    ).fetchone()
    assert row["status"] == "running"
    assert row["completed_at"] is None
    # Audit event recorded.
    ev = conn.execute(
        "SELECT COUNT(*) c FROM task_events "
        "WHERE task_id = ? AND kind = 'completion_blocked_plugin'",
        ("t_c",),
    ).fetchone()
    assert ev["c"] == 1


def test_complete_task_unblocked_still_completes(monkeypatch):
    monkeypatch.setattr(kdb, "_invoke_kanban_hook", lambda name, **kw: [])
    conn = _mk_conn()
    _insert_running(conn)
    assert kdb.complete_task(conn, "t_c", result="done") is True
    row = conn.execute(
        "SELECT status FROM tasks WHERE id = ?", ("t_c",)
    ).fetchone()
    assert row["status"] == "done"


def test_completion_auto_blocks_after_threshold(monkeypatch):
    """Kernel backstop (FIX 2): once a task has been blocked
    _MAX_COMPLETION_BLOCKS times, the next gate block auto-transitions the
    task to `blocked` (for human review) and returns False instead of
    raising forever - bounding a broken gate's retry loop. The task must end
    `blocked`, NOT `done`, and no done write must occur."""
    monkeypatch.setattr(
        kdb, "_invoke_kanban_hook",
        lambda name, **kw: (
            [{"action": "block", "message": "still failing"}]
            if name == "pre_kanban_complete" else []
        ),
    )
    conn = _mk_conn()
    _insert_running(conn)
    # Seed N == _MAX_COMPLETION_BLOCKS prior completion_blocked_plugin events
    # so the next attempt's prior-count reaches the threshold.
    for i in range(kdb._MAX_COMPLETION_BLOCKS):
        conn.execute(
            "INSERT INTO task_events (task_id, kind, payload, created_at) "
            "VALUES (?,?,?,?)",
            ("t_c", "completion_blocked_plugin", "{}", i),
        )
    conn.commit()
    # The next blocked completion trips the backstop: auto-block, return False.
    assert kdb.complete_task(conn, "t_c", result="ok") is False
    row = conn.execute(
        "SELECT status, completed_at FROM tasks WHERE id = ?", ("t_c",)
    ).fetchone()
    assert row["status"] == "blocked"     # auto-blocked for human review
    assert row["completed_at"] is None    # never marked done
    # No 'done'/'completed' transition was recorded.
    done = conn.execute(
        "SELECT COUNT(*) c FROM task_events "
        "WHERE task_id = ? AND kind = 'completed'",
        ("t_c",),
    ).fetchone()["c"]
    assert done == 0
    # The block_task path emitted a 'blocked' event.
    blk = conn.execute(
        "SELECT COUNT(*) c FROM task_events "
        "WHERE task_id = ? AND kind = 'blocked'",
        ("t_c",),
    ).fetchone()["c"]
    assert blk == 1


def test_completion_below_threshold_still_raises(monkeypatch):
    """Just under the threshold the gate still raises (the backstop only
    fires once prior blocks reach _MAX_COMPLETION_BLOCKS)."""
    monkeypatch.setattr(
        kdb, "_invoke_kanban_hook",
        lambda name, **kw: (
            [{"action": "block", "message": "still failing"}]
            if name == "pre_kanban_complete" else []
        ),
    )
    conn = _mk_conn()
    _insert_running(conn)
    for i in range(kdb._MAX_COMPLETION_BLOCKS - 1):
        conn.execute(
            "INSERT INTO task_events (task_id, kind, payload, created_at) "
            "VALUES (?,?,?,?)",
            ("t_c", "completion_blocked_plugin", "{}", i),
        )
    conn.commit()
    with pytest.raises(kdb.CompletionBlockedError):
        kdb.complete_task(conn, "t_c", result="ok")
    assert conn.execute(
        "SELECT status FROM tasks WHERE id = ?", ("t_c",)
    ).fetchone()["status"] == "running"
