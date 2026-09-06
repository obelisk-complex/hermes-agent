"""v3.8.0 — pre_verify loop-fix via the documented token path.

Pins the behaviour chosen over trigger-narrowing / auto-clear: as consecutive
refusals accumulate within a turn, the rejection message escalates to a forcing
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
    for _attempt in range(mod._max_verify_nudges() - 2):  # before the tier
        out = mod.on_pre_verify(final_response="all clear", session_id=sid, attempt=_attempt)
        assert out and out.get("decision") == "block"
        assert "RETRY BUDGET" not in out["reason"], "forcing tier fired too early"
    mod._cleanup_session(sid)


def test_forcing_directive_fires_near_exhaustion():
    mod = _load()
    sid = "tokesc-force"
    _open_gate(mod, sid)
    out = None
    for _attempt in range(mod._max_verify_nudges() - 1):  # reach the tier threshold
        out = mod.on_pre_verify(final_response="all clear", session_id=sid, attempt=_attempt)
    assert out and out.get("decision") == "block"
    m = out["reason"]
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
    for _attempt in range(mod._max_verify_nudges() + 3):  # past the escalation point
        mod.on_pre_verify(final_response="all clear", session_id=sid, attempt=_attempt)
    assert mod._get_state(sid)["pending_gate_violation"] is True, (
        "gate must stay open — auto-clear was explicitly rejected"
    )
    mod._cleanup_session(sid)


def test_token_path_clears_the_gate_and_keeps_no_counter():
    """The documented honest-override clears the gate and resets the budget."""
    mod = _load()
    sid = "tokesc-clear"
    _open_gate(mod, sid)
    mod.on_pre_verify(final_response="all clear", session_id=sid, attempt=0)  # one refusal
    out = mod.on_pre_verify(
        final_response="[GATE:ACCEPTING:child-1] endpoint was unreachable, acceptable",
        session_id=sid,
    )
    state = mod._get_state(sid)
    assert out is None, "honest-override response should be allowed through"
    assert state["pending_gate_violation"] is False, "token must clear the gate"
    assert "_on_output_blocks" not in state, (
        "the plugin must keep no retry counter of its own — the budget is core's "
        "per-turn `attempt`; a second counter is how the ladder drifted out of "
        "step with the loop enforcing it"
    )
    assert any(a == "ACCEPTED" for (_id, a, _ts) in state["_audit_log"]), (
        "acceptance must be logged to the audit trail"
    )
    mod._cleanup_session(sid)


def test_non_allclear_output_is_allowed_and_budget_is_per_turn():
    mod = _load()
    sid = "tokesc-reset"
    _open_gate(mod, sid)
    mod.on_pre_verify(final_response="all clear", session_id=sid)  # block -> count 1
    out = mod.on_pre_verify(final_response="still working on the auth fix", session_id=sid)
    assert out is None, "a non-completion-claim output is not blocked"
    # Retry accounting is per-turn and core-owned, so a fresh turn (attempt=0)
    # after several refusals must not inherit the escalated wording.
    fresh = mod.on_pre_verify(final_response="all clear", session_id=sid, attempt=0)
    assert fresh and "RETRY BUDGET" not in fresh["reason"], (
        "escalation wording leaked into a fresh turn's first refusal"
    )
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
