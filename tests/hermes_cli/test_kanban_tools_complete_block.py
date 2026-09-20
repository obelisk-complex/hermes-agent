import contextlib

import hermes_cli.kanban_db as kdb
import tools.kanban_tools as kt


# Upstream's September 2026 decomposition replaced kanban_tools._connect() with
# the `_board(board, *, quiet_close=False)` context manager, which lazily
# imports kanban_db as `kb` and yields (kb, conn). To stub it we substitute a
# context manager yielding (kdb, _FakeConn) and patch complete_task / get_task
# directly on the kdb module, exactly as the _connect stub did.


def _fake_board(*_args, **_kwargs):
    """Stand-in for kt._board: yields (kdb, _FakeConn()) like the real one."""
    @contextlib.contextmanager
    def _cm():
        conn = _FakeConn()
        try:
            yield kdb, conn
        finally:
            conn.close()
    return _cm()


def test_handle_complete_surfaces_block_message(monkeypatch):
    def boom(conn, tid, **kw):
        raise kdb.CompletionBlockedError("tests failing", tid)

    monkeypatch.setattr(kdb, "complete_task", boom, raising=True)
    monkeypatch.setattr(kt, "_board", _fake_board, raising=True)
    monkeypatch.setattr(kt, "_enforce_worker_task_ownership",
                        lambda tid: None, raising=False)
    monkeypatch.setattr(kt, "_stamp_worker_session_metadata",
                        lambda tid, md: md, raising=False)
    out = kt._handle_complete({"task_id": "t_x", "result": "ok"})
    assert "blocked by quality gate" in out
    assert "tests failing" in out
    assert "still in-flight" in out


def test_handle_complete_auto_block_reported(monkeypatch):
    """When complete_task returns False and task status is blocked, report
    the auto-block distinctly, not 'unknown id or terminal'."""
    monkeypatch.setattr(kdb, "complete_task",
                        lambda conn, tid, **kw: False, raising=True)

    class _Task:
        status = "blocked"
        goal_mode = False

    monkeypatch.setattr(kdb, "get_task",
                        lambda conn, tid: _Task(), raising=True)
    monkeypatch.setattr(kt, "_board", _fake_board, raising=True)
    monkeypatch.setattr(kt, "_enforce_worker_task_ownership",
                        lambda tid: None, raising=False)
    monkeypatch.setattr(kt, "_stamp_worker_session_metadata",
                        lambda tid, md: md, raising=False)
    out = kt._handle_complete({"task_id": "t_x", "result": "ok"})
    assert "auto-blocked for human review" in out
    assert "unknown id or already terminal" not in out


def test_board_stub_closes_the_connection(monkeypatch):
    """The real _board closes the connection in a finally block. Pin that the
    stub does too, so neither test above can pass on a leaked handle that the
    production path would have closed."""
    seen = []
    monkeypatch.setattr(kdb, "complete_task",
                        lambda conn, tid, **kw: seen.append(conn) or False,
                        raising=True)
    monkeypatch.setattr(kdb, "get_task", lambda conn, tid: None, raising=True)
    monkeypatch.setattr(kt, "_board", _fake_board, raising=True)
    monkeypatch.setattr(kt, "_enforce_worker_task_ownership",
                        lambda tid: None, raising=False)
    monkeypatch.setattr(kt, "_stamp_worker_session_metadata",
                        lambda tid, md: md, raising=False)
    kt._handle_complete({"task_id": "t_x", "result": "ok"})
    assert seen and seen[0].closed is True


class _FakeConn:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True

    def execute(self, *args, **kw):
        """Return a mock cursor whose fetchone() returns None (no task row),
        so the goal-mode judge gate is skipped and the test reaches the
        complete_task / not-ok path it's testing."""
        class _Cursor:
            def fetchone(self):
                return None
        return _Cursor()
