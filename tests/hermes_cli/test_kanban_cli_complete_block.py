import argparse

import hermes_cli.kanban_db as kdb
import hermes_cli.kanban as kc


def test_cmd_complete_block_prints_and_fails(monkeypatch, capsys):
    def boom(conn, tid, **kw):
        raise kdb.CompletionBlockedError("gate veto", tid)

    monkeypatch.setattr(kc.kb, "complete_task", boom, raising=True)

    class _Ctx:
        def __enter__(self):
            return object()
        def __exit__(self, *a):
            return False

    monkeypatch.setattr(kc.kb, "connect_closing", lambda *a, **k: _Ctx(),
                        raising=True)
    monkeypatch.setattr(kc, "_worker_run_id_for", lambda tid: None,
                        raising=False)
    args = argparse.Namespace(
        task_ids=["t_x"], result="ok", summary=None, metadata=None,
    )
    rc = kc._cmd_complete(args)
    assert rc == 1
    err = capsys.readouterr().err
    assert "blocked by quality gate" in err
    assert "gate veto" in err


def test_cmd_complete_auto_block_reported(monkeypatch, capsys):
    # complete_task returns False having auto-blocked the task (FIX 2). The
    # CLI must report the auto-block distinctly, not "unknown id or terminal".
    monkeypatch.setattr(kc.kb, "complete_task",
                        lambda conn, tid, **kw: False, raising=True)

    class _Task:
        status = "blocked"

    monkeypatch.setattr(kc.kb, "get_task",
                        lambda conn, tid: _Task(), raising=True)

    class _Ctx:
        def __enter__(self):
            return object()
        def __exit__(self, *a):
            return False

    monkeypatch.setattr(kc.kb, "connect_closing", lambda *a, **k: _Ctx(),
                        raising=True)
    monkeypatch.setattr(kc, "_worker_run_id_for", lambda tid: None,
                        raising=False)
    args = argparse.Namespace(
        task_ids=["t_x"], result="ok", summary=None, metadata=None,
    )
    rc = kc._cmd_complete(args)
    assert rc == 1
    err = capsys.readouterr().err
    assert "auto-blocked for human review" in err
    assert "unknown id or terminal state" not in err
