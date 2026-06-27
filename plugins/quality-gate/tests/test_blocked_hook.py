import blocked_hook


def _task(**over):
    base = dict(id="t-1", title="x", model_override="a", workspace_path="/tmp/ws")
    base.update(over)
    return base


def test_escalates_to_next_rung_on_retriable():
    cfg = {"quality_gate": {"model_ladder": ["a", "b", "c"]}}
    calls = []
    blocked_hook.on_fork_kanban_task_blocked(
        task=_task(model_override="a"), reason="gate_failed", config=cfg,
        # requeue_blocked_task returns True on a successful requeue; `or True`
        # records the call AND yields that truthy success so the hook takes the
        # escalation-confirmed branch (the bool return is now load-bearing).
        requeue=lambda tid, **kw: calls.append((tid, kw)) or True,
    )
    assert calls == [("t-1", {"model_override": "b"})]


def test_non_retriable_does_not_requeue():
    cfg = {"quality_gate": {"model_ladder": ["a", "b"]}}
    calls = []
    blocked_hook.on_fork_kanban_task_blocked(
        task=_task(), reason="permission_denied", config=cfg,
        requeue=lambda tid, **kw: calls.append(tid),
    )
    assert calls == []


def test_top_rung_does_not_requeue():
    cfg = {"quality_gate": {"model_ladder": ["a", "b", "c"]}}
    calls = []
    blocked_hook.on_fork_kanban_task_blocked(
        task=_task(model_override="c"), reason="gate_failed", config=cfg,
        requeue=lambda tid, **kw: calls.append(tid),
    )
    assert calls == []  # ladder exhausted, nowhere stronger to escalate


def test_exhaustion_sets_terminal_signal(caplog):
    # At the top rung the ladder is exhausted: must NOT requeue, must call the
    # update_field seam to set max_retries=0 (stop the runaway auto-requeue), and
    # must log at ERROR so an operator sees it.
    import logging
    cfg = {"quality_gate": {"model_ladder": ["a", "b", "c"]}}
    requeue_calls = []
    field_calls = []
    with caplog.at_level(logging.ERROR):
        blocked_hook.on_fork_kanban_task_blocked(
            task=_task(model_override="c"), reason="gate_failed", config=cfg,
            requeue=lambda tid, **kw: requeue_calls.append(tid),
            update_field=lambda tid, field, value: field_calls.append((tid, field, value)),
        )
    assert requeue_calls == []                       # no escalation
    assert field_calls == [("t-1", "max_retries", 0)]  # terminal signal set
    assert any(r.levelno >= logging.ERROR for r in caplog.records)


def test_exhaustion_update_field_failure_is_swallowed():
    # The terminal-signal seam failing must not raise (hook runs in the loop).
    cfg = {"quality_gate": {"model_ladder": ["a"]}}  # single rung -> next_rung None

    def boom(tid, field, value):
        raise RuntimeError("db locked")

    blocked_hook.on_fork_kanban_task_blocked(
        task=_task(model_override="a"), reason="gate_failed", config=cfg,
        update_field=boom,
    )  # must not raise


def test_object_task_escalates_correctly():
    # Mirror spawn_hook's object-task test: the blocked hook must read fields
    # from an OBJECT task (not just a dict) so escalation starts from the real
    # current rung, not silently from the bottom.
    class T:
        id = "t-9"
        title = "x"
        model_override = "a"
        workspace_path = "/tmp/ws"
    cfg = {"quality_gate": {"model_ladder": ["a", "b", "c"]}}
    calls = []
    blocked_hook.on_fork_kanban_task_blocked(
        task=T(), reason="gate_failed", config=cfg,
        requeue=lambda tid, **kw: calls.append((tid, kw)) or True,
    )
    assert calls == [("t-9", {"model_override": "b"})]


def test_requeue_failure_is_swallowed():
    cfg = {"quality_gate": {"model_ladder": ["a", "b"]}}

    def boom(tid, **kw):
        raise RuntimeError("db locked")

    # Must not raise.
    blocked_hook.on_fork_kanban_task_blocked(
        task=_task(model_override="a"), reason="timeout", config=cfg, requeue=boom,
    )


def test_matrix_notify_called_when_enabled():
    cfg = {"quality_gate": {"model_ladder": ["a", "b"],
                            "matrix": {"enabled": True, "room": "!r:hs"}}}
    sent = []
    blocked_hook.on_fork_kanban_task_blocked(
        task=_task(model_override="a"), reason="gate_failed", config=cfg,
        requeue=lambda tid, **kw: True,  # successful requeue -> notify fires
        notify_sender=lambda room, text, token: sent.append((room, text)),
    )
    assert len(sent) == 1
    assert "!r:hs" == sent[0][0]


def test_no_op_requeue_skips_notify_and_warns(caplog):
    # Graceful-False seam: when requeue_blocked_task returns False (the card was
    # NOT requeueable, e.g. upstream's BLOCK_RECURRENCE_LIMIT breaker already
    # routed it to 'triage'), the hook must NOT send a success notify and must
    # log the no-op (fail loud) instead of claiming a phantom escalation.
    import logging
    cfg = {"quality_gate": {"model_ladder": ["a", "b"],
                            "matrix": {"enabled": True, "room": "!r:hs"}}}
    sent = []
    with caplog.at_level(logging.WARNING):
        blocked_hook.on_fork_kanban_task_blocked(
            task=_task(model_override="a"), reason="gate_failed", config=cfg,
            requeue=lambda tid, **kw: False,  # not requeueable (triage/moved)
            notify_sender=lambda room, text, token: sent.append((room, text)),
        )
    assert sent == [], "no escalation notify must fire on a requeue no-op"
    assert any(r.levelno >= logging.WARNING and "NO-OP" in r.getMessage()
               for r in caplog.records), "the requeue no-op must be logged loud"
