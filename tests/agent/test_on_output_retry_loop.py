"""Tests for the on_output retry/exhaustion control flow in
agent/conversation_loop.py (round-1 QA remediation #1/#5/#8).

The retry logic lives inside the ~4000-line run_conversation() and cannot be
unit-imported, so this file does two complementary things:

1. BEHAVIOURAL MODEL — _simulate_turn() reproduces the FIXED control flow:
   _blocked and _final_validated are reset every iteration; a cleared/allowed
   response breaks; 5 consecutive blocks abort; a budget-exhaustion exit
   revalidates the response instead of leaking it. These pin the intended
   semantics so a future edit that reintroduces the sticky-flag bug fails here.

2. STRUCTURAL GUARDS — read the real source and assert the fix markers are
   present (reset INSIDE the loop; post-loop guard uses _final_validated, not a
   sticky agent attribute). These fail immediately if an upstream rebase
   relocates or drops the custom on_output hook — the regression class the
   daily sync workflow is supposed to catch but currently does not.

Runnable two ways:
    ./venv/bin/python3 tests/agent/test_on_output_retry_loop.py   # standalone
    python -m pytest tests/agent/test_on_output_retry_loop.py     # CI
"""
import os
import re

from agent._on_output_gate import (
    BLOCKED_ESCALATION_MESSAGE,
    decide_after_block,
)

_SRC_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "agent", "conversation_loop.py"
)


def _simulate_turn(on_output_results, max_iter=20):
    """Faithful model of the FIXED conversation_loop on_output retry flow.

    on_output_results[i] ∈ {"block", "allow"} — what on_output returns when the
    model produces final text on iteration i (defaults to "allow" past the end).
    """
    on_output_blocks = 0
    api_call_count = 0
    final_response = None
    exit_reason = None
    _final_validated = False
    idx = 0
    while api_call_count < max_iter:
        api_call_count += 1
        _blocked = False          # reset each iteration  (#1)
        _final_validated = False  # reset each iteration  (#5)
        final_response = f"text-{api_call_count}"   # the model produced this
        res = on_output_results[idx] if idx < len(on_output_results) else "allow"
        idx += 1
        if res == "block":
            on_output_blocks += 1
            _blocked = True
        if _blocked:
            # Exercise the REAL extracted decision, not a re-implementation.
            _decision, _esc_msg = decide_after_block(on_output_blocks)
            if _decision == "escalate":
                final_response = _esc_msg
                on_output_blocks = 0
                exit_reason = "blocked_by_policy"
                break
            continue
        _final_validated = True
        exit_reason = "text_response"
        break
    else:
        exit_reason = "budget_exhausted"
    # Post-loop revalidation (#5): on a budget-exhaustion exit the loop returns
    # the last (unvalidated) model text, which may be a leaking all-clear claim;
    # the post-loop hook re-checks it. (The 5-block abort produces its own
    # terminal message that on_output allows, so it is delivered as-is.)
    if exit_reason == "budget_exhausted" and final_response and not _final_validated:
        final_response = "REVALIDATED:" + final_response
    return {
        "exit_reason": exit_reason,
        "iterations": api_call_count,
        "final_response": final_response,
    }


def test_block_then_comply_breaks_promptly():
    """#2/#1: after one block, a compliant retry must EXIT — not loop to budget."""
    r = _simulate_turn(["block", "allow"], max_iter=20)
    assert r["exit_reason"] == "text_response"
    assert r["iterations"] == 2, f"looped {r['iterations']}x — sticky _blocked regressed"
    assert r["final_response"] == "text-2"


def test_five_blocks_abort():
    r = _simulate_turn(["block"] * 6, max_iter=20)
    assert r["exit_reason"] == "blocked_by_policy"
    assert r["final_response"] == BLOCKED_ESCALATION_MESSAGE
    assert r["iterations"] == 5


def test_budget_exhaustion_revalidates_leaking_claim():
    """#5: blocks that run out the budget must not leak the unvalidated claim."""
    r = _simulate_turn(["block", "block", "block"], max_iter=3)
    assert r["exit_reason"] == "budget_exhausted"
    assert r["final_response"].startswith("REVALIDATED:"), \
        "budget-exhaustion path leaked an unvalidated response"


def test_clean_first_response_is_validated():
    r = _simulate_turn(["allow"], max_iter=20)
    assert r["exit_reason"] == "text_response"
    assert r["iterations"] == 1
    assert not r["final_response"].startswith("REVALIDATED:")


# ── Structural guards on the real source (rebase-drift protection) ──────

def _source_lines():
    with open(os.path.normpath(_SRC_PATH), encoding="utf-8") as f:
        return f.read().splitlines()


def test_blocked_reset_inside_loop():
    """#1: `_blocked = False` must appear INSIDE the while loop, not only before
    it — otherwise a compliant retry can never break (token/turn burn)."""
    lines = _source_lines()
    while_idx = next(i for i, l in enumerate(lines)
                     if l.strip().startswith("while (api_call_count"))
    resets = [i for i, l in enumerate(lines)
              if re.match(r"\s*_blocked\s*=\s*False\b", l)]
    assert resets, "no `_blocked = False` assignment found at all"
    assert any(i > while_idx for i in resets), \
        "_blocked is never reset inside the loop (sticky-flag bug #1)"


def test_post_loop_guard_uses_validation_flag():
    """#5/#8: the post-loop on_output guard must depend on whether the returned
    response was validated this turn (_final_validated), not on a sticky agent
    attribute that skips revalidation on budget-exhaustion exits."""
    src = "\n".join(_source_lines())
    assert "_final_validated" in src, "_final_validated flag missing"
    parts = src.split("Post-loop: on_output", 1)
    assert len(parts) == 2, "post-loop on_output block not found"
    region = parts[1][:600]
    assert "not _final_validated" in region, \
        "post-loop guard does not use `not _final_validated`"
    assert "agent._on_output_fired" not in region, \
        "post-loop still reads sticky agent._on_output_fired (AttributeError risk #8)"


def test_on_output_invoked_only_on_final_text():
    """#14: the in-loop on_output invoke must stay gated by
    `if final_response and not interrupted:`, so it fires on a final-text turn
    and never on a tool-call turn. An upstream refactor that relocates it would
    silently change which outputs the gate sees (under/over-blocking)."""
    src = "\n".join(_source_lines())
    i = src.find("invoke_hook as _on_invoke")
    assert i != -1, "in-loop on_output invoke not found"
    preceding = src[max(0, i - 250):i]
    assert "if final_response and not interrupted:" in preceding, \
        "in-loop on_output no longer gated by `if final_response and not interrupted:`"


def test_exactly_two_on_output_call_sites():
    """#14: exactly two on_output invoke sites (in-loop retry + post-loop
    budget-exhaustion). A third, or a lost one, means the hook wiring drifted."""
    src = "\n".join(_source_lines())
    n = src.count('"on_output"')
    assert n == 2, f"expected exactly 2 on_output invoke sites, found {n}"


def test_union_resolution_kept_fork_counters():
    """rerere-union guard (agent/conversation_loop.py). The nightly upstream sync
    resolves a union conflict at the per-turn init block: upstream inserts a
    `_auth_pool_refresh_counts` reset (#26080) and the fork inserts its on_output
    counters at the same spot. A botched union (or a future rebase that drops the
    fork block) would still py_compile but silently break the on_output gate every
    `hermes update` consumer relies on.

    HARD-assert the fork's per-turn counter survives. The upstream-adjacency check
    is SOFT (warning only, never fails): if upstream renames/removes
    `_auth_pool_refresh_counts`, the rebase re-conflicts and aborts long before
    this test runs, so failing here would only add a confusing false alarm."""
    src = "\n".join(_source_lines())
    assert "agent._on_output_blocks = 0" in src, (
        "fork counter `agent._on_output_blocks = 0` missing from conversation_loop.py — "
        "the conversation_loop union rerere resolution dropped the fork block"
    )
    # Soft, warning-only: document that upstream's adjacent reset survived too.
    if "agent._auth_pool_refresh_counts" not in src:
        print(
            "WARNING(union-adjacency): upstream `_auth_pool_refresh_counts` reset not "
            "found near the fork counters. If upstream changed it, renew the "
            "ci/rerere-cache union entry (hash 23505fe1...) and update this guard."
        )


def test_decide_after_block_retries_under_limit():
    """v3.7.1: the extracted pure decision retries for blocks 1..LIMIT."""
    from agent._on_output_gate import decide_after_block, ON_OUTPUT_BLOCK_LIMIT
    for n in range(1, ON_OUTPUT_BLOCK_LIMIT + 1):
        assert decide_after_block(n) == ("retry", None), f"block {n} should retry"


def test_decide_after_block_escalates_on_fifth():
    """v3.7.1: the (LIMIT+1)th block escalates with an explicit BLOCKED message."""
    from agent._on_output_gate import decide_after_block, ON_OUTPUT_BLOCK_LIMIT
    decision, msg = decide_after_block(ON_OUTPUT_BLOCK_LIMIT + 1)
    assert decision == "escalate"
    assert msg and "BLOCKED" in msg and "escalat" in msg.lower(), \
        "escalation message must be an explicit BLOCKED/escalation to a human"


def test_loop_delegates_escalation_to_helper():
    """v3.7.1: conversation_loop must DELEGATE the block/escalation decision to
    agent/_on_output_gate (so the real logic is unit-tested, not re-implemented)."""
    src = "\n".join(_source_lines())
    assert "decide_after_block" in src, \
        "in-loop escalation no longer delegates to _on_output_gate.decide_after_block"


if __name__ == "__main__":
    import sys
    _tests = [v for k, v in sorted(globals().items())
              if k.startswith("test_") and callable(v)]
    _passed = _failed = 0
    for _t in _tests:
        try:
            _t()
            _passed += 1
            print(f"  ✓ {_t.__name__}")
        except AssertionError as e:
            _failed += 1
            print(f"  ✗ {_t.__name__} — {e}")
        except Exception as e:  # noqa: BLE001
            _failed += 1
            print(f"  ✗ {_t.__name__} — ERROR: {e!r}")
    print(f"\n=== {_passed} passed, {_failed} failed ===")
    sys.exit(1 if _failed else 0)
