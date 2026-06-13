# on_output Plugin Hook Implementation (v3.5.3)

## Overview

The `on_output` plugin hook fires when the LLM produces final text with no tool calls. Plugins return a dict `{"action": "block", "message": "..."}` to reject the output and force the model to retry, or `None` to let output through.

This is part of the self-check enforcer system: it allows plugins to gate final output before it reaches the user, catching policy violations, hallucinated claims, and ALL CLEAR bypasses.

## Registration

**File:** `hermes_cli/plugins.py` -- `VALID_HOOKS` set (around line 137)

```python
# on_output -- fires when the LLM finishes its final text response (no tool
# calls).  Plugins return a dict {"action": "block", "message": "..."}
# to reject the output and force the model to retry.  Return None to allow.
# Kwargs: response_text, session_id, model, platform
"on_output",
```

The hook name must match exactly between `VALID_HOOKS` and `ctx.register_hook("on_output", handler)`.

## Wiring -- conversation_loop.py

**File:** `agent/conversation_loop.py`

### Location 1: Main response handler (in-loop)

Fires after the LLM produces final text with no tool calls, guarded by `final_response and not interrupted`:

```python
                agent._on_output_fired = True
                for _ores in _on_results:
                    if isinstance(_ores, dict) and _ores.get("action") == "block":
                        _msg = _ores.get("message", "Output rejected by policy.")
                        messages.append({"role": "user", "content": _msg})
                        agent._empty_content_retries = 0
                        agent._post_tool_empty_retried = False
                        agent._on_output_blocks += 1
                        _blocked = True
                        break
                if _blocked:
                    if agent._on_output_blocks > 4:
                        final_response = ("blocked after 5 attempts - giving up")
                        agent._on_output_blocks = 0
                    else:
                        continue  # Retry outer while loop
```

**Retry mechanism:** A `_blocked` flag is set on first block. After the results loop, `if _blocked: continue` targets the outer conversation `while` loop, triggering a real LLM retry with the rejection message in history. At 5 consecutive blocks, delivers an abort message instead of retrying.

**`_on_output_fired` flag:** Set to `True` unconditionally after the hook runs, regardless of whether it blocked or allowed. This flag is used by the post-loop guard to prevent double-fire.

### Location 2: Budget-exhaustion / post-loop handler

Fires after the loop exits normally (budget exhausted), guarded by `final_response and not interrupted and not agent._on_output_fired`:

```python
    if final_response and not interrupted and not agent._on_output_fired:
        # Fire on_output for budget-exhaustion exits that bypassed the in-loop path
        _budget_results = _budget_invoke("on_output", ...)
        for _bres in _budget_results:
            if isinstance(_bres, dict) and _bres.get("action") == "block":
                final_response = _bres.get("message", "Output rejected.")
                break
```

The `not agent._on_output_fired` guard ensures normal completions that already fired the in-loop hook skip this second invocation. A block here replaces `final_response` with no retry (the loop has already exited).

### Per-turn initialization

At the top of `run_conversation()`:

```python
    agent._on_output_blocks = 0
    _blocked = False
    agent._on_output_fired = False
```

All three reset every turn. No cross-turn carry-over.

## Pitfalls

### `_on_output_fired` replaces `_blocked` for post-loop guard

The post-loop guard uses `not agent._on_output_fired`, not `not _blocked`. Using `not _blocked` only suppresses post-loop on the blocked path -- normal allowed completions (the common case) still double-fire. The separate `_on_output_fired` flag is set unconditionally after the in-loop hook runs, so it covers both allowed and blocked paths.

### `_blocked` must be at function scope

`_blocked` is referenced by both the in-loop hook and the post-loop guard. Initialise at function scope (alongside `agent._on_output_blocks = 0`) so every exit path can safely read it without UnboundLocalError.

### Retry counter resets per turn

`agent._on_output_blocks` initialises to 0 at the top of each `run_conversation()` call. A turn that exhausted 5 retries starts fresh on the next user message. Correct behaviour -- one turn's failure should not carry over.

### Budget-exhaustion path has no retry

The post-loop handler replaces `final_response` with the block message but does not re-enter the API loop. The loop has already exited. This is by design -- the model's budget summary is equivalent to "the loop ran out of iterations."

### Local import performance

The `from hermes_cli.plugins import invoke_hook` is inside the guarded block, not at module scope. Acceptable for agent-loop frequency (single-digit fires per turn). If profiling shows a hotspot, hoist to module scope.
