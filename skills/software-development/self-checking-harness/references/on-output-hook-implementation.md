# on_output Plugin Hook Implementation (v3.7.1)

## Overview

The `on_output` plugin hook fires when the agent produces its final text response with no tool calls — the moment it would report completion to the user. A plugin returns `{"action": "block", "message": "..."}` to reject that output and inject a retry turn, or `None` to let it through.

It is the only hook that gates the *final answer* (distinct from `pre_tool_call` / `pre_llm_call`, which gate tool calls and LLM turns), which makes it the right place to stop an agent claiming "done" while a delegated subagent's validation gate is still open.

## Registration

**File:** `hermes_cli/plugins.py` — the `VALID_HOOKS` set

```python
# on_output — fires when the LLM finishes its final text response (no tool
# calls). Plugins return {"action": "block", "message": "..."} to reject the
# output and force a retry. Return None to allow.
# Kwargs: response_text, session_id, model, platform
"on_output",
```

The hook name must match between `VALID_HOOKS` and `ctx.register_hook("on_output", handler)`.

## Wiring — conversation_loop.py

**File:** `agent/conversation_loop.py`

### Per-turn state

`agent._on_output_blocks` (the block counter) resets to 0 once per turn. `_blocked` and `_final_validated` reset at the top of every loop **iteration** (not just per turn) — a per-turn-only reset was a gate-clearing bug where a compliant retry after one block could never break and the turn burned to budget (fixed in v3.6.0).

### Location 1: in-loop, after final text with no tool calls

Guarded by `if final_response and not interrupted:`. A block increments the per-turn counter; the block/escalation decision is then delegated to a pure helper:

```python
                    for _ores in _on_results:
                        if isinstance(_ores, dict) and _ores.get("action") == "block":
                            messages.append({"role": "user", "content": _msg})
                            agent._on_output_blocks += 1
                            _blocked = True
                            break
                    if _blocked:
                        # v3.7.1: the block/escalation decision is an importable
                        # pure helper so it is unit-tested directly.
                        from agent._on_output_gate import decide_after_block
                        _decision, _esc_msg = decide_after_block(agent._on_output_blocks)
                        if _decision == "escalate":
                            final_response = _esc_msg   # explicit BLOCKED escalation
                            agent._on_output_blocks = 0
                        else:
                            continue   # retry the outer loop
                    else:
                        _final_validated = True
```

`agent/_on_output_gate.py` is the single source of truth for the threshold and the terminal message:

```python
ON_OUTPUT_BLOCK_LIMIT = 4   # the 5th block escalates

BLOCKED_ESCALATION_MESSAGE = (
    "⚠️ BLOCKED — escalating to a human. The self-check gate blocked this "
    "output 5 times (unresolved FAIL / verification). The task is NOT complete "
    "and needs human attention; it is not being silently marked done."
)

def decide_after_block(block_count):
    if block_count > ON_OUTPUT_BLOCK_LIMIT:
        return "escalate", BLOCKED_ESCALATION_MESSAGE
    return "retry", None
```

**Retry → escalation:** a block sets `_blocked` and `continue`s the outer loop (a real LLM retry with the rejection message in history). The 5th block (`> 4`) does not retry — it replaces the response with an explicit **BLOCKED escalation to a human** (the task is not silently marked done) and resets the counter. This was made an explicit BLOCKED escalation in v3.7.0 and extracted to the helper in v3.7.1.

### Location 2: post-loop (budget-exhaustion / non-standard exit)

Guarded by `if final_response and not interrupted and not _final_validated:`. `_final_validated` is True only when the in-loop hook ALLOWED the exact response being returned this turn, so a normal validated completion skips this second invocation, while a budget-exhaustion exit (which leaves the last, unvalidated model text) is re-checked here:

```python
    if final_response and not interrupted and not _final_validated:
        _budget_results = _budget_invoke("on_output", ...)
        for _bres in _budget_results:
            if isinstance(_bres, dict) and _bres.get("action") == "block":
                final_response = _bres.get("message", "...")
                break
```

A block here replaces `final_response` with no retry (the loop has already exited).

## Design notes

### `_final_validated`, not a sticky "fired" flag (v3.6.0)

The post-loop guard depends on whether the returned response was *validated this turn* (`_final_validated`), not on whether the hook merely *fired*. An earlier design used a sticky `_on_output_fired` flag set unconditionally; it was removed in v3.6.0 because it skipped revalidation on budget-exhaustion exits, letting a leaking all-clear slip through. `_final_validated` is set only on the in-loop allow path and reset every iteration.

### `_blocked` / `_final_validated` reset every iteration

Both reset at the top of each loop iteration, not just per turn. A per-turn-only reset (the original bug) left `_blocked` sticky, so a compliant retry after one block could never break (round-1 QA #1).

### Retry counter resets per turn

`agent._on_output_blocks` resets to 0 per turn — one turn's exhaustion does not carry to the next user message.

### Budget-exhaustion path has no retry

The post-loop handler replaces `final_response` but does not re-enter the API loop (it has already exited). This is by design — the model's budget summary is equivalent to "the loop ran out of iterations."

### Decision extracted for testability (v3.7.1)

The block-count / escalation decision lives in `agent/_on_output_gate.py` so it can be unit-tested directly, rather than only via a re-implementation in the test plus source-string guards (`run_conversation` is ~4000 lines and not importable in isolation).
