# The `pre_verify` completion gate: core wiring

How the self-check gate stops a completion claim, and which parts of that are
upstream's versus this fork's.

Superseded `on-output-hook-implementation.md`, which described a parallel
`on_output` hook the fork carried in `agent/conversation_loop.py`. Upstream has
since built the same reject-and-retry mechanism, so the fork's copy is gone and
the gate rides upstream's. What remains fork-side are two gaps upstream's
version does not cover.

## What upstream provides

`pre_verify` is dispatched from `agent/turn_stop_gates.py:apply_stop_gates`,
which runs three gates in order: verify-on-stop, the `pre_verify` plugin hook,
then the kanban terminal guard. Any one of them can refuse the answer.

On a refusal the mechanism is:

- the draft answer is appended as an interim assistant row,
- the hook's message is appended as a synthetic user turn,
- `final_response` is cleared and the verdict carries `continue_turn=True`,
- the loop runs another iteration.

The hook receives `final_response`, `changed_paths`, `attempt`, `coding`,
`session_id`, `platform` and `model`, and returns either
`{"decision": "block", "reason": "..."}` or the equivalent
`{"action": "continue", "message": "..."}`. Both shapes are accepted; the
`decision`/`block` spelling reads as "block the stop" and is what this fork's
plugin uses, because under the old `on_output` hook "block" meant the same
thing and the alternative spelling inverts the word's sense.

The retry budget is `agent.max_verify_nudges`, counted per turn as `attempt`
and reset by `agent/turn_context.py`'s per-turn state reset.

## What the fork adds, and why

**1. The gate fires on turns that edited nothing.**

Upstream guards on `_edited` — the turn's file mutations — so the hook is only
consulted after code changes. That is too narrow for this gate. The case it
exists for is an orchestrator that dispatched a subagent, got a `FAIL` back, and
then claims completion; that turn typically mutates no files at all, so an
edits-only guard never asks the hook on precisely the turn that matters.

The fork widens the guard to `(_edited or api_call_count > 1)`.
`api_call_count == 1` means one API call and no tools — a bare "hi" — which
stays exempt so trivial turns pay no hook dispatch. Anything that ran a tool or
spawned a child is at least 2.

The clause order is load-bearing: the integer compare short-circuits before
`has_hook`, which performs two lazy imports.

**2. Exhaustion escalates instead of going quiet.**

Upstream stops asking once `attempt >= max_verify_nudges()`. From
`apply_stop_gates`'s point of view "the hook is satisfied" and "the budget ran
out" are then the same observation — a silent `None` — and the unverified
answer ships.

`agent/turn_stop_gates.pre_verify_terminal_substitute` closes that: at
exhaustion it asks the hook once more, out of band, and whatever comes back
replaces the final text rather than nudging, because there is no iteration left
to nudge into. The plugin returns its BLOCKED escalation there instead of a
retry prompt.

There are two exhaustion routes and both are covered:

- **nudge budget spent** — handled at the tail of `apply_stop_gates`, with the
  substitute surfacing through `StopGateVerdict.final_response` and the one-line
  `final_response = _sg.final_response` in `agent/turn_final_response.py`.
- **iteration budget spent while the gate was still nudging** — upstream's
  `_resolve_budget_fallback` in `agent/turn_finalizer.py` restores the withheld
  draft verbatim, which is exactly how an unverified claim reaches the user
  marked done. The same helper is consulted there.

## Where the policy lives

The escalation text is `_BLOCKED_ESCALATION_MESSAGE` in
`plugins/self-check-enforcer/__init__.py`. It is deliberately in the plugin, not
in `agent/`: it is fork policy, and policy text in a core module upstream owns
re-conflicts on every sync.

The threshold is read live via `agent.verify_hooks.max_verify_nudges()` rather
than mirrored as a plugin constant. Two thresholds that can disagree is how a
ladder silently drifts out of step with the loop enforcing it — which is what
happened with the old `ON_OUTPUT_BLOCK_LIMIT`.

## If the gate goes quiet

The failure mode to suspect first is the precondition. `(_edited or
api_call_count > 1)` sits inside a file upstream rewrites wholesale, so a sync
that restores upstream's `_edited`-only line leaves the plugin loaded, its hook
registered, and the gate never firing on the turns it was built for. Nothing
errors. `tests/agent/test_pre_verify_gate.py` asserts the non-editing turn
fires; that is the tripwire.
