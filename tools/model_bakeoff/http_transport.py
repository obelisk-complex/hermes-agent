"""Default live transports (httpx), imported lazily so the offline test suite
never needs httpx. The bakeoff is non-streaming (SPEC §5), so TTFT is not
separable from total latency here and is reported as None; total latency is
measured by the caller around the await.
"""
from __future__ import annotations


async def http_transport(url, headers, json, timeout):
    """POST a chat/completions request. Returns (status, body_json_or_None, ttft)."""
    import httpx

    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(url, headers=headers, json=json)
        try:
            body = r.json()
        except Exception:  # noqa: BLE001 - non-JSON error body; surface status to caller
            body = None
        return r.status_code, body, None


async def list_models(base_url, api_key, timeout=30):
    """GET /v1/models. Returns a set of served model ids, or None if unavailable."""
    import httpx

    url = base_url.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {api_key}", "User-Agent": "hermes-cli/0.17.0"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.get(url, headers=headers)
            if r.status_code != 200:
                return None
            data = r.json().get("data", [])
            return {m.get("id") for m in data if m.get("id")}
    except Exception:  # noqa: BLE001 - listing is best-effort; None => "could not verify"
        return None
