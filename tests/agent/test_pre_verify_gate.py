"""The fork's completion gate, as wired onto upstream's ``pre_verify`` mechanism.

Replaces test_on_output_retry_loop.py, which guarded a parallel gate the fork
carried in agent/conversation_loop.py. That gate is gone; upstream's
agent/turn_stop_gates.py does the reject-and-retry now. Two things upstream does
NOT do are fork edits, and both are what this file pins:

  1. upstream only consults the hook when the turn edited a file, so the case the
     gate exists for (an orchestrator that dispatched a failing child and then
     claims done, mutating nothing) would never reach it;
  2. upstream goes quiet when the nudge budget runs out, shipping the very answer
     the gate refused.

Both edits live in files upstream rewrites wholesale, so a sync can silently
revert either one and leave the plugin loaded, its hook registered, and the gate
never firing. These are behavioural: they call the gate and assert on what it
does, so they fail when the mechanism stops working rather than when a string
moves.
"""

import pytest

from agent import turn_stop_gates as tsg


class _Agent:
    """Minimal stand-in for the turn-loop agent surface the gates touch."""

    def __init__(self, edited=()):
        self.session_id = "s1"
        self.platform = ""
        self.model = "m"
        self.quiet_mode = True
        self._turn_file_mutation_paths = set(edited)
        self._pre_verify_nudges = 0
        self._resolved_is_coding = False
        self._session_messages = []
        self.emitted_interim = []

    def _emit_interim_assistant_message(self, msg):
        self.emitted_interim.append(msg)

    def _flush_messages_to_session_db(self, *a, **k):
        pass

    def _interim_content_was_streamed(self, _text):
        return False

    def _emit_status(self, *a, **k):
        pass

    def _safe_print(self, *a, **k):
        pass


@pytest.fixture
def quiet_sibling_gates(monkeypatch):
    """Silence verify-on-stop and the kanban guard so pre_verify is what is under test."""
    monkeypatch.setattr(tsg, "_verify_on_stop_nudge", lambda agent: None)
    monkeypatch.setattr(tsg, "_kanban_stop_nudge", lambda agent, messages: None)


@pytest.fixture
def hook(monkeypatch):
    """Register a recording ``pre_verify`` hook; returns the call log."""
    calls = []

    def _fake_message(**kwargs):
        calls.append(kwargs)
        return calls_return["value"]

    calls_return = {"value": "RESOLVE THIS FIRST"}
    import hermes_cli.lifecycle
    import hermes_cli.plugins

    monkeypatch.setattr(hermes_cli.lifecycle, "has_hook", lambda name: name == "pre_verify")
    monkeypatch.setattr(hermes_cli.plugins, "get_pre_verify_continue_message", _fake_message)
    return {"calls": calls, "returns": calls_return}


def _run(agent, *, api_call_count, final_response="ALL CLEAR, task complete."):
    messages = []
    return tsg.apply_stop_gates(
        agent, {"role": "assistant", "content": final_response},
        final_response=final_response, messages=messages,
        conversation_history=None, pending_verification_response=None,
        pending_verification_response_previewed=False,
        api_call_count=api_call_count,
    ), messages


def test_gate_fires_on_a_turn_that_edited_nothing(quiet_sibling_gates, hook):
    """The orchestrator case: dispatched a child, no file edits, claims done.

    Upstream's `_edited`-only guard never reaches the hook here. If a sync
    restores that guard this fails, which is the whole point of the test.
    """
    agent = _Agent(edited=())
    verdict, messages = _run(agent, api_call_count=2)

    assert hook["calls"], "pre_verify hook was never consulted on a non-editing turn"
    assert verdict.continue_turn is True
    assert any(m.get("role") == "user" and "RESOLVE THIS FIRST" in m.get("content", "")
               for m in messages), "no synthetic nudge row was appended"
    assert verdict.final_response is None, "the refused draft must not stay as the answer"


def test_gate_stays_quiet_on_a_bare_turn(quiet_sibling_gates, hook):
    """A one-call, no-tool turn ("hi") must not pay a hook dispatch or get nudged.

    Pairs with the test above: together they are satisfiable only by a gate that
    is both live and correctly scoped.
    """
    agent = _Agent(edited=())
    verdict, messages = _run(agent, api_call_count=1)

    assert not hook["calls"], "bare turn should not consult the pre_verify hook"
    assert verdict.continue_turn is False
    assert messages == []


def test_gate_still_fires_after_edits(quiet_sibling_gates, hook):
    """Upstream's own trigger must keep working — the fork widens it, not replaces it."""
    agent = _Agent(edited=("a.py",))
    verdict, _ = _run(agent, api_call_count=1)

    assert hook["calls"], "an editing turn must still reach the hook"
    assert verdict.continue_turn is True


def test_exhausted_budget_substitutes_the_answer(quiet_sibling_gates, hook, monkeypatch):
    """Budget spent and the gate still refusing: replace the answer, don't ship it.

    Upstream stops asking at this point and the unverified draft flows through.
    """
    monkeypatch.setattr("agent.verify_hooks.max_verify_nudges", lambda config=None: 2)
    hook["returns"]["value"] = "BLOCKED - escalating to a human."
    agent = _Agent(edited=())
    agent._pre_verify_nudges = 2  # budget spent

    verdict, messages = _run(agent, api_call_count=3)

    assert verdict.continue_turn is False, "no iterations left; must not nudge again"
    assert verdict.final_response == "BLOCKED - escalating to a human."
    assert messages == [], "substitution replaces the answer, it does not nudge"


def test_satisfied_gate_leaves_the_answer_alone(quiet_sibling_gates, hook, monkeypatch):
    """The ordinary path: budget spent but the hook is content -> answer untouched."""
    monkeypatch.setattr("agent.verify_hooks.max_verify_nudges", lambda config=None: 2)
    hook["returns"]["value"] = None
    agent = _Agent(edited=())
    agent._pre_verify_nudges = 2

    verdict, _ = _run(agent, api_call_count=3, final_response="Done, all checks passed.")

    assert verdict.continue_turn is False
    assert verdict.final_response == "Done, all checks passed."


def test_iteration_budget_exit_rechecks_a_leaking_claim(monkeypatch):
    """The second exhaustion route.

    When the loop runs out of iterations while a gate is still refusing, upstream
    restores the withheld draft verbatim (`_resolve_budget_fallback`). That is how
    an unverified claim reaches the user marked done, so the gate gets a final say.
    """
    from agent import turn_finalizer

    monkeypatch.setattr(
        tsg, "pre_verify_terminal_substitute",
        lambda agent, final_response, attempt, api_call_count: "BLOCKED - escalating.",
    )

    class _BudgetAgent(_Agent):
        max_iterations = 3
        iteration_budget = type("_B", (), {"remaining": 0})()
        _response_was_previewed = False

    agent = _BudgetAgent()
    final_response, exit_reason, preserved = turn_finalizer._resolve_budget_fallback(
        agent, final_response=None, api_call_count=3, interrupted=False, failed=False,
        messages=[], _turn_exit_reason="unknown",
        _pending_verification_response="ALL CLEAR, task complete.",
        _pending_verification_response_previewed=False, logger=tsg.logger,
    )

    assert preserved is True
    assert final_response == "BLOCKED - escalating.", (
        "the refused draft was restored verbatim on the budget-exhaustion path"
    )
