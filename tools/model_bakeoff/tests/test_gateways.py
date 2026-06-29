"""gateway resolution from env (SPEC §3, §11). Offline."""
from __future__ import annotations

import pytest

from tools.model_bakeoff import gateways


def test_resolve_specific_env_vars():
    env = {"BAKEOFF_OPENCODE_ZEN_URL": "https://zen/v1", "BAKEOFF_OPENCODE_ZEN_KEY": "zk"}
    conn = gateways.resolve("opencode-zen", env)
    assert conn.ok
    assert conn.base_url == "https://zen/v1" and conn.api_key == "zk"


def test_resolve_falls_back_to_single_gateway_pair():
    env = {"BAKEOFF_GATEWAY_URL": "https://local/v1", "BAKEOFF_GATEWAY_KEY": "k"}
    conn = gateways.resolve("opencode-go", env)
    assert conn.ok and conn.base_url == "https://local/v1"


def test_missing_config_is_not_ok_and_reports_fields():
    conn = gateways.resolve("ollama-cloud", {})
    assert not conn.ok
    assert "base_url" in conn.missing and "api_key" in conn.missing


def test_unknown_gateway_raises():
    with pytest.raises(KeyError):
        gateways.resolve("nope", {})
