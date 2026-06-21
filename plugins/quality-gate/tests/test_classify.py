import time

import classify
import tiers


class _Resp:
    """Mirrors the real PluginLlmCompleteResult: generated text on .text."""
    def __init__(self, text):
        self.text = text


class _FakeLLM:
    """Mirrors the real PluginLlm.complete(messages, **kw) -> result-with-.text.

    The real facade is SYNCHRONOUS, takes OpenAI-shaped messages (a list of
    {"role","content"} dicts), and returns an OBJECT whose text is on .text.
    This fake asserts the messages shape so a regression to a bare-string call
    is caught.
    """
    def __init__(self, reply="standard", delay=0.0, raises=False):
        self._reply, self._delay, self._raises = reply, delay, raises

    def complete(self, messages, **kw):
        assert isinstance(messages, list) and messages and \
            isinstance(messages[0], dict) and "content" in messages[0], \
            "classify must call complete() with OpenAI-shaped messages, not a bare string"
        if self._delay:
            time.sleep(self._delay)
        if self._raises:
            raise RuntimeError("llm down")
        return _Resp(self._reply)


def test_sidecar_roundtrip(tmp_workspace):
    p = classify.write_tier(tmp_workspace, "thorough")
    assert p == tmp_workspace / ".hermes" / "quality-gate" / "tier"
    assert classify.read_tier(tmp_workspace) == "thorough"


def test_read_tier_missing_is_none(tmp_workspace):
    assert classify.read_tier(tmp_workspace) is None


def test_write_tier_gitignores_dir(tmp_workspace):
    # The sidecar dir must self-gitignore (mirrors evidence.py) so a worktree
    # workspace's git status stays clean and the gate does not self-block.
    classify.write_tier(tmp_workspace, "standard")
    gi = tmp_workspace / ".hermes" / "quality-gate" / ".gitignore"
    assert gi.exists()
    assert gi.read_text(encoding="utf-8").strip() == "*"


def test_should_classify_guards_review_and_terminal():
    assert classify.should_classify("queued", "task") is True
    assert classify.should_classify("review", "task") is False
    assert classify.should_classify("done", "task") is False
    assert classify.should_classify("queued", "terminal") is False


def test_classify_returns_valid_tier():
    t = classify.classify_tier("Add feature", "body", llm=_FakeLLM("thorough"))
    assert t == "thorough"
    assert t in tiers.TIERS


def test_classify_uses_messages_and_dot_text():
    # Regression guard for the real PluginLlm contract: complete(messages) ->
    # result.text. A recording fake captures the call shape.
    seen = {}

    class _Recorder:
        def complete(self, messages, **kw):
            seen["messages"] = messages
            seen["kw"] = kw
            return _Resp("standard")

    t = classify.classify_tier("Title here", "Body here", llm=_Recorder())
    assert t == "standard"
    msgs = seen["messages"]
    assert isinstance(msgs, list) and msgs[-1]["role"] == "user"
    assert "Title here" in msgs[-1]["content"]


def test_classify_plain_string_response_still_works():
    # Defensive: if a facade ever returns a bare string instead of an object
    # with .text, _response_text must still extract it (no crash).
    class _StrLLM:
        def complete(self, messages, **kw):
            return "quick"
    assert classify.classify_tier("x", "y", llm=_StrLLM()) == "quick"


def test_classify_invalid_output_falls_back():
    t = classify.classify_tier("x", "y", llm=_FakeLLM("banana"))
    assert t == tiers.DEFAULT_TIER


def test_classify_timeout_falls_back_fast():
    start = time.monotonic()
    # LLM sleeps 2.0s; timeout 0.5s. Must return the default well before 2s,
    # proving the singleton-executor + fut.cancel() path (not shutdown(wait=True),
    # which would block ~2s). A 2.0s sleep (vs 10s) keeps the orphaned worker
    # short-lived so the small pool is not exhausted across repeated timeout
    # tests in one session; the gap (0.5s timeout << 2.0s sleep) is still ample.
    t = classify.classify_tier("x", "y", llm=_FakeLLM("thorough", delay=2.0), timeout_s=0.5)
    elapsed = time.monotonic() - start
    assert t == tiers.DEFAULT_TIER
    assert elapsed < 1.5


def test_classify_llm_error_falls_back():
    t = classify.classify_tier("x", "y", llm=_FakeLLM(raises=True))
    assert t == tiers.DEFAULT_TIER


def test_executor_is_module_singleton():
    import concurrent.futures
    assert isinstance(classify._EXECUTOR, concurrent.futures.ThreadPoolExecutor)
