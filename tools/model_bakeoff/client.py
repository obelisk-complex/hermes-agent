"""Async OpenAI-compatible caller for one (model, prompt) (SPEC §5).

The HTTP POST is injected (a `transport` coroutine), so request-building and
response-parsing are unit-testable offline with no network; the default transport
that uses httpx is imported only when a live call is actually made.

Integrity-relevant behaviour:
- User-Agent: hermes-cli/<v> on every request (OpenCode's WAF 403s a bare UA).
- temperature is governed SOLELY by spec.omit_temp (SPEC §3 PM1).
- cache-busting: a fresh UUID nonce appended to the prompt + a no-store header.
- TTFT and total latency recorded separately; a sub-100ms response is flagged as a
  suspected cache hit and retried once with a fresh nonce.
- thinking/reasoning tokens recorded distinctly and billed as output.
"""
from __future__ import annotations

import time
import uuid
from typing import Awaitable, Callable, Optional

from .models import CallResult, ModelSpec

UA = "hermes-cli/0.17.0"
CACHE_HIT_THRESHOLD_S = 0.1

# transport(url=, headers=, json=, timeout=) -> (status_code, response_json, ttft_s)
Transport = Callable[..., Awaitable[tuple]]


def make_nonce() -> str:
    return uuid.uuid4().hex


def build_headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": UA,
        "Content-Type": "application/json",
        "Cache-Control": "no-store",
        "X-Bakeoff-Nonce": make_nonce(),
    }


def build_payload(spec: ModelSpec, prompt: str, nonce: str,
                  reasoning_extras: Optional[dict] = None) -> dict:
    # nonce as a trailing comment: forces a cache miss without changing task semantics.
    user = f"{prompt}\n\n<!-- nonce:{nonce} -->"
    payload: dict = {
        "model": spec.wire_id,
        "messages": [{"role": "user", "content": user}],
        "max_tokens": spec.max_tokens,
        "stream": False,
    }
    if not spec.omit_temp:
        payload["temperature"] = 0
    if reasoning_extras:
        payload.update(reasoning_extras)
    return payload


def _usage(resp: dict) -> tuple[int, int, int]:
    u = resp.get("usage", {}) or {}
    prompt_t = int(u.get("prompt_tokens", 0) or 0)
    completion_t = int(u.get("completion_tokens", 0) or 0)
    details = u.get("completion_tokens_details", {}) or {}
    thinking_t = int(details.get("reasoning_tokens", 0) or 0)
    return prompt_t, completion_t, thinking_t


def _content(resp: dict) -> str:
    try:
        return resp["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return ""


def cost_usd(spec: ModelSpec, prompt_t: int, completion_t: int, thinking_t: int) -> float:
    if not spec.is_metered or spec.price_in_per_m is None or spec.price_out_per_m is None:
        return 0.0  # subscription => $0 marginal; unpriced metered resolved at preflight
    out_t = completion_t + thinking_t  # thinking tokens billed as output
    return (prompt_t * spec.price_in_per_m + out_t * spec.price_out_per_m) / 1_000_000.0


def parse_response(spec: ModelSpec, task_id: str, status: int,
                   resp, ttft_s, total_s) -> CallResult:
    if status != 200 or not isinstance(resp, dict):
        return CallResult(model_key=spec.key, task_id=task_id, ok=False,
                          total_latency_s=total_s, error=f"http {status}")
    prompt_t, completion_t, thinking_t = _usage(resp)
    return CallResult(
        model_key=spec.key, task_id=task_id, ok=True, raw_response=_content(resp),
        ttft_s=ttft_s, total_latency_s=total_s,
        prompt_tokens=prompt_t, completion_tokens=completion_t, thinking_tokens=thinking_t,
        cache_hit=(total_s is not None and total_s < CACHE_HIT_THRESHOLD_S),
        cost_usd=cost_usd(spec, prompt_t, completion_t, thinking_t),
    )


async def call(spec: ModelSpec, task_id: str, prompt: str, api_key: str, base_url: str,
               transport: Transport, reasoning_extras: Optional[dict] = None,
               retry_on_cache_hit: bool = True) -> CallResult:
    url = base_url.rstrip("/") + "/chat/completions"

    async def _once() -> CallResult:
        nonce = make_nonce()
        headers = build_headers(api_key)
        payload = build_payload(spec, prompt, nonce, reasoning_extras)
        start = time.monotonic()
        try:
            status, resp, ttft = await transport(
                url=url, headers=headers, json=payload, timeout=spec.api_timeout_s)
        except Exception as exc:  # noqa: BLE001 - surface transport failures, do not swallow
            return CallResult(model_key=spec.key, task_id=task_id, ok=False,
                              total_latency_s=time.monotonic() - start, error=f"{type(exc).__name__}: {exc}")
        return parse_response(spec, task_id, status, resp, ttft, time.monotonic() - start)

    result = await _once()
    if result.ok and result.cache_hit and retry_on_cache_hit:
        retried = await _once()
        retried.error = (retried.error + " retried-after-suspected-cache-hit").strip()
        return retried
    return result
