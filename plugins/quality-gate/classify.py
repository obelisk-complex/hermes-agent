"""Aux-LLM tier classifier + tier sidecar persistence.

There is NO tier column on the kanban ``tasks`` table (confirmed), so the
chosen tier is written to a per-card SIDECAR file under the workspace.

CRITICAL — the timeout uses a MODULE-LEVEL singleton ThreadPoolExecutor and
``fut.cancel()``. We do NOT use the executor as a context manager: a
``with ThreadPoolExecutor() as ex:`` block calls ``shutdown(wait=True)`` on
exit, which BLOCKS until the (slow) LLM call returns — defeating the timeout
entirely. The singleton + cancel pattern returns promptly and lets the
orphaned worker die on its own.
"""
from __future__ import annotations

import concurrent.futures
import logging
from pathlib import Path
from typing import Any, Optional, Union

from . import tiers

logger = logging.getLogger(__name__)

# Module-level singleton — NEVER used as a context manager (see module docstring).
_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="qg-classify",
)

# Cards whose status/kind means "do not classify".
_SKIP_STATUS = {"review", "done", "blocked", "cancelled", "archived"}
_SKIP_KIND = {"review", "terminal"}

_PROMPT = (
    "Classify the rigor tier needed to complete this software task. "
    "Reply with exactly ONE word from: quick, standard, thorough.\n"
    "  quick    = trivial/docs/config, lint-only is enough.\n"
    "  standard = normal feature/bugfix, needs lint + tests.\n"
    "  thorough = risky/core/release work, needs lint + tests + typecheck + build.\n"
    "Title: {title}\nBody: {body}\nTier:"
)


def tier_sidecar_path(workspace: Union[str, Path]) -> Path:
    return Path(workspace) / ".hermes" / "quality-gate" / "tier"


def write_tier(workspace: Union[str, Path], tier: str) -> Path:
    p = tier_sidecar_path(workspace)
    p.parent.mkdir(parents=True, exist_ok=True)
    # Self-gitignore the sidecar dir (mirrors evidence.py). On a worktree
    # workspace this dir is a real git tree, so without it the sidecar shows
    # as an untracked file in ``git status --porcelain`` and the gate's own
    # githygiene check would see it, set hygiene_clean=False, and the gate
    # would self-block the card. Idempotent: written only when missing.
    gi = p.parent / ".gitignore"
    if not gi.exists():
        gi.write_text("*\n", encoding="utf-8")
    p.write_text(tiers.normalise_tier(tier) + "\n", encoding="utf-8")
    return p


def read_tier(workspace: Union[str, Path]) -> Optional[str]:
    p = tier_sidecar_path(workspace)
    if not p.exists():
        return None
    try:
        return tiers.normalise_tier(p.read_text(encoding="utf-8").strip())
    except OSError:
        return None


def should_classify(status: str, kind: str) -> bool:
    """False for review/terminal/done-ish cards; True for actionable ones."""
    return (status or "").lower() not in _SKIP_STATUS and (kind or "").lower() not in _SKIP_KIND


def _response_text(resp: Any) -> str:
    """Extract generated text from the LLM facade's return value.

    The real ``PluginLlm.complete`` returns a ``PluginLlmCompleteResult`` whose
    text is on ``.text`` (VERIFIED agent/plugin_llm.py). Be defensive: accept a
    ``.text`` attribute, or a bare string, else empty (-> DEFAULT_TIER upstream).
    """
    text = getattr(resp, "text", None)
    if isinstance(text, str):
        return text
    if isinstance(resp, str):
        return resp
    return ""


def _parse_tier(raw: Any) -> str:
    text = _response_text(raw)
    word = text.strip().split()[0].lower() if text.strip() else ""
    return word if word in tiers.TIERS else tiers.DEFAULT_TIER


def classify_tier(title: str, body: str, *, llm: Any, timeout_s: float = 12.0) -> str:
    """Classify a card's tier via the aux-LLM, fail-safe to DEFAULT_TIER.

    Calls the host facade with the VERIFIED contract — SYNCHRONOUS
    ``complete(messages: list[dict]) -> result`` with text on ``.text`` (see
    agent/plugin_llm.py) — NOT a bare-string prompt returning a string. Timeout
    enforced with the module singleton executor + fut.cancel() (best-effort;
    cannot kill a running thread, but fut.result returns promptly).
    """
    prompt = _PROMPT.format(title=title or "", body=(body or "")[:2000])
    messages = [{"role": "user", "content": prompt}]

    def _call() -> str:
        return _parse_tier(llm.complete(messages))

    fut = _EXECUTOR.submit(_call)
    try:
        return fut.result(timeout=timeout_s)
    except concurrent.futures.TimeoutError:
        fut.cancel()  # best-effort; orphan the worker, return promptly
        logger.warning("quality-gate: tier classification timed out; using %s", tiers.DEFAULT_TIER)
        return tiers.DEFAULT_TIER
    except Exception as exc:
        logger.warning("quality-gate: tier classification failed (%s); using %s", exc, tiers.DEFAULT_TIER)
        return tiers.DEFAULT_TIER
