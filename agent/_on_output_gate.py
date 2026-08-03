"""Pure decision helpers for the on_output retry / 5-block escalation gate.

Extracted from ``conversation_loop.run_conversation`` so the block-count and
escalation decision can be unit-tested directly: the loop function is ~4000
lines and cannot be imported in isolation, which previously left this logic
covered only by a re-implementation in the test plus source-string guards (a
behaviour-changing edit that kept the markers would have passed). This module
is the single source of truth for the threshold and the escalation message;
both ``conversation_loop`` and the tests import it.

Behaviour is identical to the inlined version it replaces.
"""

# After this many on_output blocks within a single turn, stop retrying and
# escalate: the (LIMIT + 1)-th block ends the turn with the BLOCKED message.
ON_OUTPUT_BLOCK_LIMIT = 4

# The terminal text returned when the retry budget is exhausted. It is an
# explicit BLOCKED / escalation-to-a-human, not a vague "could not complete"
# warning — the task is NOT silently marked done.
BLOCKED_ESCALATION_MESSAGE = (
    "⚠️ BLOCKED — escalating to a human. The "
    "self-check gate blocked this output 5 times "
    "(unresolved FAIL / verification). The task is "
    "NOT complete and needs human attention; it is "
    "not being silently marked done. The gate stays "
    "OPEN — resolve it honestly by fixing and "
    "re-dispatching with verifies_task=<id>, or by "
    "acknowledging each open violation on record with "
    "[GATE:ACCEPTING:<id>]."
)


def decide_after_block(block_count: int) -> tuple[str, str | None]:
    """Decide what to do after on_output has blocked the current turn's output.

    ``block_count`` is the per-turn block counter AFTER it has been incremented
    for the block just seen. Returns ``(action, message)``:

    - ``("escalate", BLOCKED_ESCALATION_MESSAGE)`` once the count exceeds
      ``ON_OUTPUT_BLOCK_LIMIT`` (the 5th block) — the caller replaces the
      response with ``message`` and resets the counter.
    - ``("retry", None)`` otherwise — the caller continues the loop for another
      attempt.
    """
    if block_count > ON_OUTPUT_BLOCK_LIMIT:
        return "escalate", BLOCKED_ESCALATION_MESSAGE
    return "retry", None
