"""Resolve per-gateway (base_url, api_key) from the environment (SPEC §3, §11).

Config is the runtime source of truth: base URLs and keys come from env vars
(hermes populates these from .env / Bitwarden via load_hermes_dotenv), NOT from
hardcoded defaults. Missing config fails LOUD at preflight rather than silently
hitting a wrong host. A single BAKEOFF_GATEWAY_* pair acts as a fallback for the
case where one local hermes gateway proxies every backend.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

# gateway -> (base_url_env, api_key_env)
GATEWAY_ENV = {
    "opencode-go": ("BAKEOFF_OPENCODE_GO_URL", "BAKEOFF_OPENCODE_GO_KEY"),
    "opencode-zen": ("BAKEOFF_OPENCODE_ZEN_URL", "BAKEOFF_OPENCODE_ZEN_KEY"),
    "ollama-cloud": ("BAKEOFF_OLLAMA_CLOUD_URL", "BAKEOFF_OLLAMA_CLOUD_KEY"),
}
FALLBACK_URL_ENV = "BAKEOFF_GATEWAY_URL"
FALLBACK_KEY_ENV = "BAKEOFF_GATEWAY_KEY"


@dataclass
class GatewayConn:
    gateway: str
    base_url: str | None
    api_key: str | None

    @property
    def ok(self) -> bool:
        return bool(self.base_url and self.api_key)

    @property
    def missing(self) -> list[str]:
        out = []
        if not self.base_url:
            out.append("base_url")
        if not self.api_key:
            out.append("api_key")
        return out


def resolve(gateway: str, env: dict | None = None) -> GatewayConn:
    env = os.environ if env is None else env
    if gateway not in GATEWAY_ENV:
        raise KeyError(f"unknown gateway: {gateway}")
    url_var, key_var = GATEWAY_ENV[gateway]
    base = env.get(url_var) or env.get(FALLBACK_URL_ENV)
    key = env.get(key_var) or env.get(FALLBACK_KEY_ENV)
    return GatewayConn(gateway, base, key)
