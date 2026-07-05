"""v3.7.2 — on_output loop-fix via the documented token path.

Pins the behaviour chosen over trigger-narrowing / auto-clear: as consecutive
on_output blocks accumulate, the rejection message escalates to a forcing
directive that drives the agent onto the existing clearance paths
(verifies_task / [GATE:ACCEPTING:<id>]) BEFORE the 5th-block human escalation —
without weakening detection-first or the "not silently marked done" guarantee.

Stdlib-only; runs standalone or under pytest.
"""
import importlib.util
import os

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PLUGIN = os.path.join(_REPO, "plugins", "self-check-enforcer", "__init__.py")


def _load():
    spec = importlib.util.spec_from_file_location("self_check_enforcer_tokesc", _PLUGIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _open_gate(mod, sid):
    """Put one open violation into the session state, as on_subagent_stop would."""
    state = mod._get_state(sid)
    state["pending_gate_violation"] = True
    state["violations"] = {"child-1": "[child-1] — 1 failed check(s):\n  ✗ FAIL: x"}
    state["last_violation_detail"] = state["violations"]["child-1"]
    state["_on_output_blocks"] = 0
    return state


def test_detection_first_is_unchanged():
    """The narrowing we declined must NOT have shipped: bare \\bFAIL\\b still gates."""
    mod = _load()
    assert mod._FAIL_PATTERN.pattern == r"\bFAIL\b", (
        "v3.7.2 must not narrow the FAIL trigger — detection-first is intentional"
    )


def test_early_blocks_have_no_forcing_directive():
    mod = _load()
    sid = "tokesc-early"
    _open_gate(mod, sid)
    for _ in range(mod._BLOCK_LIMIT - 2):  # blocks 1..(limit-2) i.e. before the tier
        out = mod.on_output(response_text="all clear", session_id=sid)
        assert out and out.get("action") == "block"
        assert "RETRY BUDGET" not in out["message"], "forcing tier fired too early"
    mod._cleanup_session(sid)


def test_forcing_directive_fires_near_exhaustion():
    mod = _load()
    sid = "tokesc-force"
    _open_gate(mod, sid)
    out = None
    for _ in range(mod._BLOCK_LIMIT - 1):  # reach the tier threshold
        out = mod.on_output(response_text="all clear", session_id=sid)
    assert out and out.get("action") == "block"
    m = out["message"]
    assert "RETRY BUDGET" in m, "no forcing directive at budget exhaustion"
    assert "[GATE:ACCEPTING:<id>]" in m, "directive must point at the token path"
    assert "verifies_task=<id>" in m, "directive must offer the mechanical-clear path"
    assert "child-1" in m, "directive must surface the open violation id"
    mod._cleanup_session(sid)


def test_gate_is_not_auto_cleared_by_blocking():
    """No auto-clear: blocking N times must leave the gate OPEN (not silently done)."""
    mod = _load()
    sid = "tokesc-open"
    _open_gate(mod, sid)
    for _ in range(mod._BLOCK_LIMIT + 3):  # well past the human-escalation point
        mod.on_output(response_text="all clear", session_id=sid)
    assert mod._get_state(sid)["pending_gate_violation"] is True, (
        "gate must stay open — auto-clear was explicitly rejected"
    )
    mod._cleanup_session(sid)


def test_token_path_clears_and_resets_counter():
    """The documented honest-override clears the gate and resets the budget."""
    mod = _load()
    sid = "tokesc-clear"
    _open_gate(mod, sid)
    mod.on_output(response_text="all clear", session_id=sid)  # one block
    out = mod.on_output(
        response_text="[GATE:ACCEPTING:child-1] endpoint was unreachable, acceptable",
        session_id=sid,
    )
    state = mod._get_state(sid)
    assert out is None, "honest-override response should be allowed through"
    assert state["pending_gate_violation"] is False, "token must clear the gate"
    assert state["_on_output_blocks"] == 0, "counter must reset once the gate clears"
    assert any(a == "ACCEPTED" for (_id, a, _ts) in state["_audit_log"]), (
        "acceptance must be logged to the audit trail"
    )
    mod._cleanup_session(sid)


def test_non_allclear_output_resets_counter():
    mod = _load()
    sid = "tokesc-reset"
    _open_gate(mod, sid)
    mod.on_output(response_text="all clear", session_id=sid)  # block -> count 1
    out = mod.on_output(response_text="still working on the auth fix", session_id=sid)
    assert out is None, "a non-completion-claim output is not blocked"
    assert mod._get_state(sid)["_on_output_blocks"] == 0, "counter resets on allow"
    mod._cleanup_session(sid)


if __name__ == "__main__":
    import sys
    _tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    _p = _f = 0
    for _t in _tests:
        try:
            _t(); _p += 1; print(f"  ✓ {_t.__name__}")
        except AssertionError as e:
            _f += 1; print(f"  ✗ {_t.__name__} — {e}")
    print(f"\n=== {_p} passed, {_f} failed ===")
    sys.exit(1 if _f else 0)
