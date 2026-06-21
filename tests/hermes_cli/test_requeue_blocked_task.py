import sqlite3

import hermes_cli.kanban_db as kdb


def _mk_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(kdb.SCHEMA_SQL)
    return conn


def _insert_blocked(conn, tid="t_rq", failures=3):
    conn.execute(
        "INSERT INTO tasks (id, title, status, created_at, "
        "consecutive_failures, last_failure_error, model_override) "
        "VALUES (?,?,?,?,?,?,?)",
        (tid, "x", "blocked", 0, failures, "boom", "weak-model"),
    )
    conn.commit()


def test_requeue_escalates_model_and_resets_counters():
    conn = _mk_conn()
    _insert_blocked(conn)
    ok = kdb.requeue_blocked_task(
        conn, "t_rq", model_override="claude-opus-4-8", reason="escalate"
    )
    assert ok is True
    row = conn.execute(
        "SELECT status, model_override, consecutive_failures, "
        "last_failure_error FROM tasks WHERE id = ?", ("t_rq",)
    ).fetchone()
    assert row["status"] == "ready"          # no parents → ready
    assert row["model_override"] == "claude-opus-4-8"
    assert row["consecutive_failures"] == 0
    assert row["last_failure_error"] is None


def test_requeue_without_model_keeps_existing():
    conn = _mk_conn()
    _insert_blocked(conn)
    assert kdb.requeue_blocked_task(conn, "t_rq", reason="retry") is True
    row = conn.execute(
        "SELECT status, model_override FROM tasks WHERE id = ?", ("t_rq",)
    ).fetchone()
    assert row["status"] == "ready"
    assert row["model_override"] == "weak-model"   # untouched
    # The requeued audit event is written even without a model escalation,
    # inside the SAME atomic txn as the status flip (FIX 1).
    ev = conn.execute(
        "SELECT payload FROM task_events WHERE task_id = ? AND kind = 'requeued'",
        ("t_rq",),
    ).fetchone()
    assert ev is not None
    import json
    payload = json.loads(ev["payload"])
    assert payload["escalation_applied"] is False
    assert payload["reason"] == "retry"


def test_requeue_non_blocked_returns_false():
    conn = _mk_conn()
    conn.execute(
        "INSERT INTO tasks (id, title, status, created_at) VALUES (?,?,?,?)",
        ("t_run", "x", "running", 0),
    )
    conn.commit()
    # running is not blocked/scheduled → unblock_task rowcount 0 → False.
    assert kdb.requeue_blocked_task(conn, "t_run", model_override="m") is False
    # model_override must NOT be applied when the requeue did not happen.
    row = conn.execute(
        "SELECT model_override FROM tasks WHERE id = ?", ("t_run",)
    ).fetchone()
    assert row["model_override"] is None


def test_requeue_with_undone_parent_lands_in_todo():
    conn = _mk_conn()
    conn.execute(
        "INSERT INTO tasks (id, title, status, created_at) VALUES (?,?,?,?)",
        ("t_parent", "p", "running", 0),
    )
    _insert_blocked(conn, tid="t_child")
    conn.execute(
        "INSERT INTO task_links (parent_id, child_id) VALUES (?,?)",
        ("t_parent", "t_child"),
    )
    conn.commit()
    assert kdb.requeue_blocked_task(
        conn, "t_child", model_override="claude-opus-4-8"
    ) is True
    row = conn.execute(
        "SELECT status, model_override FROM tasks WHERE id = ?", ("t_child",)
    ).fetchone()
    # Parent not done → unblock_task routes to todo, not ready.
    assert row["status"] == "todo"
    # Escalation still applied even when gated to todo.
    assert row["model_override"] == "claude-opus-4-8"


def test_requeue_is_atomic_status_and_model_in_one_txn():
    # The escalation lands in the same UPDATE as the status flip: after the
    # delegated unblock_task returns, the model is already the escalated one
    # (no intermediate ready-on-old-model state to observe). We assert the
    # post-state directly; the single-UPDATE design is what makes it atomic.
    conn = _mk_conn()
    _insert_blocked(conn)
    assert kdb.requeue_blocked_task(
        conn, "t_rq", model_override="claude-opus-4-8"
    ) is True
    row = conn.execute(
        "SELECT status, model_override FROM tasks WHERE id = ?", ("t_rq",)
    ).fetchone()
    assert row["status"] == "ready"
    assert row["model_override"] == "claude-opus-4-8"
    # requeued audit event records escalation_applied=True.
    ev = conn.execute(
        "SELECT payload FROM task_events WHERE task_id = ? AND kind = 'requeued'",
        ("t_rq",),
    ).fetchone()
    import json
    assert json.loads(ev["payload"])["escalation_applied"] is True


def test_unblock_task_backward_compat_two_arg():
    # The original two-arg call must behave exactly as before (no model write,
    # no requeued event; requeue_event defaults to None).
    conn = _mk_conn()
    _insert_blocked(conn)
    assert kdb.unblock_task(conn, "t_rq") is True
    row = conn.execute(
        "SELECT status, model_override FROM tasks WHERE id = ?", ("t_rq",)
    ).fetchone()
    assert row["status"] == "ready"
    assert row["model_override"] == "weak-model"   # untouched
    # No requeued event from a bare unblock_task — only requeue_blocked_task
    # (which passes requeue_event) emits it.
    n = conn.execute(
        "SELECT COUNT(*) c FROM task_events "
        "WHERE task_id = ? AND kind = 'requeued'",
        ("t_rq",),
    ).fetchone()["c"]
    assert n == 0


def test_unblock_task_with_model_override_kwarg():
    conn = _mk_conn()
    _insert_blocked(conn)
    assert kdb.unblock_task(
        conn, "t_rq", model_override="claude-opus-4-8"
    ) is True
    row = conn.execute(
        "SELECT status, model_override FROM tasks WHERE id = ?", ("t_rq",)
    ).fetchone()
    assert row["status"] == "ready"
    assert row["model_override"] == "claude-opus-4-8"
