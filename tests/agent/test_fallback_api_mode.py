import types

import pytest

import agent.chat_completion_helpers as cch


class _FakeClient:
    def __init__(self, base_url):
        self.base_url = base_url
        self.api_key = "k"
        self._custom_headers = None
        self.default_headers = None


class _FakeAgent:
    """Minimal duck-typed agent for try_activate_fallback's api_mode path."""

    def __init__(self, chain):
        self._fallback_chain = chain
        self._fallback_index = 0
        self._fallback_activated = False
        self._rate_limited_until = 0.0
        self._primary_runtime = {"provider": "openai"}
        self.provider = "openai"
        self.model = "gpt-4o"
        self.base_url = "https://api.openai.com/v1"
        self.api_mode = "chat_completions"
        self.api_key = "k"
        self._config_context_length = None
        self._credential_pool = None
        self._transport_cache = {}
        self.context_compressor = None
        self.captured_api_mode = None

    # --- detection helpers (normally AIAgent methods) ------------------
    def _is_azure_openai_url(self, url=None):
        return "azure" in (url or self.base_url)

    def _is_direct_openai_url(self, url=None):
        return "api.openai.com" in (url or "")

    def _provider_model_requires_responses_api(self, model, provider=None):
        return False

    # --- post-decision plumbing (made cheap no-ops) --------------------
    def _try_activate_fallback(self):
        return False

    def _anthropic_prompt_cache_policy(self, **kw):
        return (False, False)

    def _ensure_lmstudio_runtime_loaded(self):
        pass

    def _buffer_status(self, *_a, **_k):
        pass


def _install_common_stubs(monkeypatch, agent):
    """Stub resolve_provider_client + capture api_mode at the swap point."""
    def fake_resolve(provider, model=None, raw_codex=False,
                     explicit_base_url=None, explicit_api_key=None):
        base = explicit_base_url or "https://api.openai.com/v1"
        return _FakeClient(base), model

    monkeypatch.setattr(
        "agent.auxiliary_client.resolve_provider_client", fake_resolve,
        raising=True,
    )
    monkeypatch.setattr(
        "hermes_cli.model_normalize.normalize_model_for_provider",
        lambda m, p: m, raising=False,
    )
    monkeypatch.setattr(cch, "get_provider_request_timeout",
                        lambda *a, **k: None, raising=False)
    monkeypatch.setattr(cch, "rewrite_prompt_model_identity",
                        lambda *a, **k: None, raising=False)
    # Capture api_mode the instant it is assigned onto the agent.
    def capturing_setattr(self, name, value):
        if name == "api_mode" and getattr(self, "_capture_on", False):
            object.__setattr__(self, "captured_api_mode", value)
        object.__setattr__(self, name, value)

    monkeypatch.setattr(_FakeAgent, "__setattr__", capturing_setattr,
                        raising=True)
    object.__setattr__(agent, "_capture_on", True)


def test_explicit_entry_api_mode_wins(monkeypatch):
    # Entry says chat_completions; detection (direct openai url) would pick
    # codex_responses. Explicit must win.
    agent = _FakeAgent([
        {"provider": "openai", "model": "gpt-5",
         "base_url": "https://api.openai.com/v1",
         "api_mode": "chat_completions"},
    ])
    _install_common_stubs(monkeypatch, agent)
    assert cch.try_activate_fallback(agent) is True
    assert agent.captured_api_mode == "chat_completions"


def test_auto_detection_when_no_entry_api_mode(monkeypatch):
    # No api_mode on entry; direct openai url → detection picks codex_responses.
    agent = _FakeAgent([
        {"provider": "openai", "model": "gpt-5",
         "base_url": "https://api.openai.com/v1"},
    ])
    _install_common_stubs(monkeypatch, agent)
    assert cch.try_activate_fallback(agent) is True
    assert agent.captured_api_mode == "codex_responses"


def test_activation_warning_does_not_leak_base_url_secret(monkeypatch, caplog):
    # A credential embedded in the entry base_url must NOT reach the WARNING
    # log - only the hostname is logged (Revision Log §7b-secret).
    secret = "supersecrettoken"
    agent = _FakeAgent([
        {"provider": "openai", "model": "gpt-5",
         "base_url": f"https://{secret}@proxy.example.com/v1",
         "api_mode": "chat_completions"},
    ])

    def fake_resolve(p, model=None, raw_codex=False,
                     explicit_base_url=None, explicit_api_key=None):
        # Echo the entry base_url so fb_base_url carries the secret.
        return _FakeClient(explicit_base_url
                           or f"https://{secret}@proxy.example.com/v1"), model

    monkeypatch.setattr(
        "agent.auxiliary_client.resolve_provider_client", fake_resolve,
        raising=True,
    )
    monkeypatch.setattr(
        "hermes_cli.model_normalize.normalize_model_for_provider",
        lambda m, p: m, raising=False,
    )
    monkeypatch.setattr(cch, "get_provider_request_timeout",
                        lambda *a, **k: None, raising=False)
    monkeypatch.setattr(cch, "rewrite_prompt_model_identity",
                        lambda *a, **k: None, raising=False)
    with caplog.at_level("WARNING"):
        cch.try_activate_fallback(agent)
    activation = [r.getMessage() for r in caplog.records
                  if "Fallback activating" in r.getMessage()]
    assert activation, "expected a 'Fallback activating' WARNING"
    joined = " ".join(activation)
    assert secret not in joined
    assert "proxy.example.com" in joined


@pytest.mark.parametrize("provider,base,model,expected", [
    ("openai-codex", "https://api.openai.com/v1", "gpt-5", "codex_responses"),
    ("anthropic",    "https://api.anthropic.com", "claude-x", "anthropic_messages"),
    ("openai",       "https://x.azure.com/openai", "gpt-5", "chat_completions"),
    ("bedrock",      "https://bedrock-runtime.us-east-1.amazonaws.com",
     "anthropic.claude", "bedrock_converse"),
])
def test_detection_tree_unchanged(monkeypatch, provider, base, model, expected):
    # Pins the else-branch: when NO entry api_mode, each provider/url still
    # resolves to its historical transport.
    agent = _FakeAgent([
        {"provider": provider, "model": model, "base_url": base},
    ])
    # Make the fake client echo the entry base_url so detection sees it.
    def fake_resolve(p, model=None, raw_codex=False,
                     explicit_base_url=None, explicit_api_key=None):
        return _FakeClient(explicit_base_url or base), model
    monkeypatch.setattr(
        "agent.auxiliary_client.resolve_provider_client", fake_resolve,
        raising=True,
    )
    monkeypatch.setattr(
        "hermes_cli.model_normalize.normalize_model_for_provider",
        lambda m, p: m, raising=False,
    )
    monkeypatch.setattr(cch, "get_provider_request_timeout",
                        lambda *a, **k: None, raising=False)
    monkeypatch.setattr(cch, "rewrite_prompt_model_identity",
                        lambda *a, **k: None, raising=False)
    def cap(self, name, value):
        if name == "api_mode" and getattr(self, "_capture_on", False):
            object.__setattr__(self, "captured_api_mode", value)
        object.__setattr__(self, name, value)
    monkeypatch.setattr(_FakeAgent, "__setattr__", cap, raising=True)
    agent = _FakeAgent([{"provider": provider, "model": model, "base_url": base}])
    object.__setattr__(agent, "_capture_on", True)
    cch.try_activate_fallback(agent)
    assert agent.captured_api_mode == expected
