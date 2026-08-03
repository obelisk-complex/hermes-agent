import notify


def test_disabled_by_default():
    assert notify.matrix_enabled(None) is False
    assert notify.matrix_enabled({}) is False
    assert notify.matrix_enabled({"quality_gate": {"matrix": {"enabled": True}}}) is False  # no room


def test_enabled_requires_enabled_and_room():
    cfg = {"quality_gate": {"matrix": {"enabled": True, "room": "!abc:hs"}}}
    assert notify.matrix_enabled(cfg) is True


def test_notify_noop_when_disabled():
    sent = []
    ok = notify.notify({}, "hello", sender=lambda *a, **k: sent.append(a))
    assert ok is False
    assert sent == []  # sender never called


def test_notify_calls_sender_when_enabled():
    cfg = {"quality_gate": {"matrix": {"enabled": True, "room": "!r:hs", "token": "T"}}}
    calls = []

    def sender(room, text, token):
        calls.append((room, text, token))
        return True

    ok = notify.notify(cfg, "evidence here", sender=sender)
    assert ok is True
    assert calls == [("!r:hs", "evidence here", "T")]


def test_notify_never_raises_on_sender_error():
    cfg = {"quality_gate": {"matrix": {"enabled": True, "room": "!r:hs"}}}

    def boom(*a, **k):
        raise RuntimeError("network down")

    ok = notify.notify(cfg, "x", sender=boom)
    assert ok is False  # swallowed + logged, not raised


def test_notify_warns_when_enabled_but_no_room(caplog):
    """When enabled=true but no room is configured, must log WARNING and return False."""
    cfg = {"quality_gate": {"matrix": {"enabled": True}}}  # no room
    with caplog.at_level("WARNING"):
        result = notify.notify(cfg, "hello")
    assert result is False
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("misconfigured" in m for m in warnings), (
        f"expected a misconfigured WARNING; got: {warnings}"
    )


def test_home_room():
    cfg = {"quality_gate": {"matrix": {"room": "!r:hs"}}}
    assert notify.home_room(cfg) == "!r:hs"
    assert notify.home_room({}) is None
