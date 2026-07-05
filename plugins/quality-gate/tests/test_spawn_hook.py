import classify
import spawn_hook
import tiers


class _Resp:
    def __init__(self, text): self.text = text


class _LLM:
    # Mirrors the real PluginLlm.complete(messages) -> result-with-.text contract.
    def __init__(self, reply): self._r = reply
    def complete(self, messages, **kw):
        assert isinstance(messages, list) and messages[-1]["role"] == "user"
        return _Resp(self._r)


def _task(tmp, **over):
    base = dict(id="t-1", title="Add core feature", body="risky",
                status="queued", kind="task", workspace_path=str(tmp))
    base.update(over)
    return base


def test_returns_initial_rung_override(tmp_workspace):
    cfg = {"quality_gate": {"model_ladder": ["a", "b", "c"]}}
    out = spawn_hook.on_pre_kanban_spawn(
        task=_task(tmp_workspace), config=cfg, llm=_LLM("thorough"),
    )
    assert out["model_override"] == "b"  # thorough on a 3-rung ladder: cap_idx=1 -> ladder[1]
    assert out["tier"] == "thorough"


def test_writes_tier_sidecar(tmp_workspace):
    cfg = {"quality_gate": {"model_ladder": ["a", "b"]}}
    spawn_hook.on_pre_kanban_spawn(task=_task(tmp_workspace), config=cfg, llm=_LLM("quick"))
    assert classify.read_tier(tmp_workspace) == "quick"


def test_review_card_is_guarded(tmp_workspace):
    out = spawn_hook.on_pre_kanban_spawn(
        task=_task(tmp_workspace, status="review"), config={}, llm=_LLM("thorough"),
    )
    assert out is None
    assert classify.read_tier(tmp_workspace) is None  # not classified


def test_no_llm_uses_default_tier(tmp_workspace):
    cfg = {"quality_gate": {"model_ladder": ["a", "b"]}}
    out = spawn_hook.on_pre_kanban_spawn(task=_task(tmp_workspace), config=cfg, llm=None)
    assert out["tier"] == tiers.DEFAULT_TIER
    assert classify.read_tier(tmp_workspace) == tiers.DEFAULT_TIER


def test_object_task_supported(tmp_workspace):
    class T:
        id = "t-2"; title = "x"; body = "y"; status = "queued"
        kind = "task"; workspace_path = str(tmp_workspace)
    out = spawn_hook.on_pre_kanban_spawn(
        task=T(), config={"quality_gate": {"model_ladder": ["a", "b"]}}, llm=_LLM("standard"),
    )
    assert out["model_override"] == "a"


def test_empty_ladder_returns_none_for_override(tmp_workspace):
    # An empty configured ladder falls back to DEFAULT_LADDER, so a rung exists.
    out = spawn_hook.on_pre_kanban_spawn(
        task=_task(tmp_workspace), config={"quality_gate": {"model_ladder": []}}, llm=_LLM("standard"),
    )
    assert "model_override" in out  # default ladder non-empty


def test_quick_tier_starts_at_weakest_rung(tmp_workspace):
    # Proves tier-awareness (not just the cap): quick starts at ladder[0], where
    # thorough on the SAME 3-rung ladder starts at ladder[1] ("b").
    cfg = {"quality_gate": {"model_ladder": ["a", "b", "c"]}}
    out = spawn_hook.on_pre_kanban_spawn(
        task=_task(tmp_workspace), config=cfg, llm=_LLM("quick"),
    )
    assert out["model_override"] == "a"
    assert out["tier"] == "quick"


def test_escalated_card_not_reset_on_respawn(tmp_workspace):
    # Regression: a re-queued card that blocked_hook escalated already carries a
    # model_override. The spawn hook must LEAVE it (returning None) rather than
    # recompute the initial rung and reset the escalation back down, and must not
    # re-classify (no tier sidecar written this call).
    cfg = {"quality_gate": {"model_ladder": ["a", "b", "c"]}}
    out = spawn_hook.on_pre_kanban_spawn(
        task=_task(tmp_workspace, model_override="c"), config=cfg, llm=_LLM("quick"),
    )
    assert out is None
    assert classify.read_tier(tmp_workspace) is None
