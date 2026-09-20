import sqlite3

import pytest

import hermes_cli.kanban_db as kdb


def _mk_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(kdb.SCHEMA_SQL)
    conn.execute(
        "INSERT INTO tasks (id, title, status, created_at) VALUES (?,?,?,?)",
        ("t_u", "x", "ready", 0),
    )
    conn.commit()
    return conn


def test_update_allowed_field():
    conn = _mk_conn()
    assert kdb.update_task_field(conn, "t_u", "assignee", "worker-1") is True
    row = conn.execute(
        "SELECT assignee FROM tasks WHERE id = ?", ("t_u",)
    ).fetchone()
    assert row["assignee"] == "worker-1"


@pytest.mark.parametrize("field", ["model_override", "workspace_path", "branch_name"])
def test_fields_owned_by_upstream_setters_are_rejected(field):
    """These three left the allowlist deliberately.

    Upstream now ships dedicated setters (set_model_override,
    set_workspace_path, set_branch_name) that validate, refuse archived tasks,
    write the companion provider_override, emit a typed audit event and fire
    notify_task_updated. A bare ``UPDATE tasks SET <field>`` through here
    skipped all of it, and any plugin can call this function — so the generic
    path must refuse rather than quietly bypass those guarantees."""
    conn = _mk_conn()
    with pytest.raises(ValueError) as excinfo:
        kdb.update_task_field(conn, "t_u", field, "x")
    assert "mutable allowlist" in str(excinfo.value)
    assert "set_model_override" in str(excinfo.value), (
        "the refusal must point the caller at the setter that owns the field"
    )


def test_update_priority_int():
    conn = _mk_conn()
    assert kdb.update_task_field(conn, "t_u", "priority", 5) is True
    assert conn.execute(
        "SELECT priority FROM tasks WHERE id = ?", ("t_u",)
    ).fetchone()["priority"] == 5


def test_update_unknown_field_rejected():
    conn = _mk_conn()
    with pytest.raises(ValueError):
        kdb.update_task_field(conn, "t_u", "status; DROP TABLE tasks", "x")
    # Table intact.
    assert conn.execute("SELECT COUNT(*) c FROM tasks").fetchone()["c"] == 1


def test_update_pk_rejected():
    conn = _mk_conn()
    with pytest.raises(ValueError):
        kdb.update_task_field(conn, "t_u", "id", "t_new")


def test_update_claim_column_rejected():
    conn = _mk_conn()
    with pytest.raises(ValueError):
        kdb.update_task_field(conn, "t_u", "claim_lock", "stolen")


def test_update_status_rejected():
    # status transitions only via complete_task/block_task/unblock_task/
    # requeue_blocked_task - never a raw column write (F4/B4).
    conn = _mk_conn()
    with pytest.raises(ValueError):
        kdb.update_task_field(conn, "t_u", "status", "done")
    # Status untouched.
    assert conn.execute(
        "SELECT status FROM tasks WHERE id = ?", ("t_u",)
    ).fetchone()["status"] == "ready"


def test_update_result_on_done_task_rejected():
    conn = _mk_conn()
    conn.execute(
        "INSERT INTO tasks (id, title, status, created_at, result) "
        "VALUES (?,?,?,?,?)",
        ("t_done", "x", "done", 0, "original output"),
    )
    conn.commit()
    with pytest.raises(ValueError):
        kdb.update_task_field(conn, "t_done", "result", "tampered")
    assert conn.execute(
        "SELECT result FROM tasks WHERE id = ?", ("t_done",)
    ).fetchone()["result"] == "original output"


def test_update_result_on_running_task_allowed():
    conn = _mk_conn()  # t_u is 'ready'
    assert kdb.update_task_field(conn, "t_u", "result", "wip") is True
    assert conn.execute(
        "SELECT result FROM tasks WHERE id = ?", ("t_u",)
    ).fetchone()["result"] == "wip"


def test_update_missing_task_returns_false():
    conn = _mk_conn()
    assert kdb.update_task_field(conn, "t_absent", "priority", 1) is False


def test_update_skills_serializes_list():
    conn = _mk_conn()
    assert kdb.update_task_field(conn, "t_u", "skills", ["qa", "sdlc"]) is True
    raw = conn.execute("SELECT skills FROM tasks WHERE id = ?", ("t_u",)).fetchone()["skills"]
    import json as _j; assert _j.loads(raw) == ["qa", "sdlc"]


def test_update_skills_accepts_none():
    conn = _mk_conn()
    assert kdb.update_task_field(conn, "t_u", "skills", None) is True
    assert conn.execute("SELECT skills FROM tasks WHERE id = ?", ("t_u",)).fetchone()["skills"] is None
