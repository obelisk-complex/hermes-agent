#!/usr/bin/env python3
"""
Escalation Ladder — automatic model-tier routing for delegate_task.

Usage (from agent context):
    from scripts.escalate_delegate import classify, set_tier, restore_config, FailureCache

    # Classify the task
    tier, method = classify(goal, context)

    # Set delegation config for the chosen tier
    original = set_tier(tier)

    # Agent calls delegate_task(goal=..., context=...) here

    # On failure, escalate:
    cache = FailureCache()
    cache.record_failure(task_hash, tier, error)
    next_tier = min(tier + 1, 4)
    if next_tier > 4:
        return error  # terminal — no more tiers

    # Restore original config when done
    restore_config(original)
"""

import json
import hashlib
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# ── Tier Configuration ──────────────────────────────────────────────────────

TIER_CONFIG = {
    1: {  # TRIVIAL
        "model": "deepseek-v4-flash:cloud",
        "provider": "ollama",
        "base_url": "http://localhost:11434/v1",
    },
    2: {  # MODERATE
        "model": "deepseek-v4-pro:cloud",
        "provider": "ollama",
        "base_url": "http://localhost:11434/v1",
    },
    3: {  # COMPLEX — same model as MODERATE (intentional, headroom for future)
        "model": "deepseek-v4-pro:cloud",
        "provider": "ollama",
        "base_url": "http://localhost:11434/v1",
    },
    4: {  # FRONTIER
        "model": "anthropic/claude-opus-4.8",
        "provider": "openrouter",
        "base_url": "",  # OpenRouter default
    },
}

# Frontier fallback when Tier 4 has infrastructure failure (503, rate limit)
FRONTIER_FALLBACK = {
    "model": "openai/gpt-5.6-sol",
    "provider": "openrouter",
    "base_url": "",
}

# Router models (local, free)
ROUTER_PRIMARY = "gemma4:9b"
ROUTER_FALLBACK = "qwen3.6:27b"

# ── Heuristic Pre-filter ────────────────────────────────────────────────────

def classify_heuristic(goal: str, context: str) -> int | None:
    """Zero-cost keyword/pattern matching. Returns tier 1-4 or None if inconclusive."""
    goal_lower = goal.lower()

    # FRONTIER keywords
    frontier_keywords = ["audit", "security review", "threat model",
                         "penetration test", "vulnerability assessment"]
    for kw in frontier_keywords:
        if kw in goal_lower:
            return 4

    # COMPLEX keywords
    complex_keywords = ["architecture", "redesign", "migration",
                        "refactor across", "cross-system", "multi-service"]
    for kw in complex_keywords:
        if kw in goal_lower:
            return 3

    # TRIVIAL: very short goal
    if len(goal) < 50:
        return 1

    # Context mentions 5+ distinct files → COMPLEX
    file_pattern = re.findall(r'(?:^|\s)([\w./-]+\.(?:py|js|ts|java|go|rs|md|yaml|json|toml))', context)
    if len(set(file_pattern)) >= 5:
        return 3

    return None  # inconclusive


# ── Router Model Classifier ─────────────────────────────────────────────────

ROUTER_PROMPT = """Classify this coding task into one of four difficulty levels:
1-TRIVIAL (single-file edit, simple query, <50 chars goal)
2-MODERATE (multi-file change, moderate complexity)
3-COMPLEX (architecture change, new feature, debugging)
4-FRONTIER (security audit, plan audit, cross-system integration)

Examples:
"Add docstring to calculate_total()" → 1
"Refactor error handling in the auth module" → 2
"Implement OAuth2 flow with refresh token rotation" → 3
"Audit this deployment plan for security gaps" → 4

Task: {goal}
Context: {context}

Respond with ONLY the number (1-4)."""


def _call_ollama(model: str, prompt: str, timeout: int = 10) -> str | None:
    """Call a local Ollama model via stdin to avoid argv exposure. Returns response text or None on failure."""
    try:
        result = subprocess.run(
            ["ollama", "run", model],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def classify_router(goal: str, context: str) -> int:
    """Classify task difficulty using a local router model. Falls back to Tier 2 on failure."""
    prompt = ROUTER_PROMPT.format(goal=goal, context=context[:500])

    # Try primary router model
    response = _call_ollama(ROUTER_PRIMARY, prompt)
    if response is None:
        # Try fallback
        response = _call_ollama(ROUTER_FALLBACK, prompt)

    if response is None:
        # Both router models unavailable — default to Tier 2
        print("[escalation-ladder] WARNING: Both router models unavailable, defaulting to Tier 2",
              file=sys.stderr)
        return 2

    # Parse response — extract LAST integer 1-4 (avoid first-match trap with verbose/CoT routers)
    matches = re.findall(r'\b([1-4])\b', response)
    if matches:
        tier = int(matches[-1])
        return tier

    # Malformed response — default to Tier 2
    print(f"[escalation-ladder] WARNING: Router returned unparseable output, defaulting to Tier 2. "
          f"Raw: {response[:100]}", file=sys.stderr)
    return 2


# ── Classification Cache ────────────────────────────────────────────────────

_classification_cache: dict[str, tuple[int, float]] = {}
"""In-memory cache: task_hash → (tier, timestamp). 5-minute TTL, max 1000 entries."""
_CLASSIFICATION_CACHE_MAX = 1000


def _prune_classification_cache():
    """Evict expired entries and enforce max size (oldest-first)."""
    now = time.time()
    # Remove expired
    expired = [k for k, (_, ts) in _classification_cache.items() if now - ts >= 300]
    for k in expired:
        del _classification_cache[k]
    # Enforce max size (drop oldest)
    if len(_classification_cache) > _CLASSIFICATION_CACHE_MAX:
        sorted_entries = sorted(_classification_cache.items(), key=lambda x: x[1][1])
        for k, _ in sorted_entries[:len(_classification_cache) - _CLASSIFICATION_CACHE_MAX]:
            del _classification_cache[k]


def classify(goal: str, context: str, force_tier: int | None = None) -> tuple[int, str]:
    """
    Classify a task into a difficulty tier.

    Returns (tier, method) where method is one of:
    'force', 'heuristic', 'router', 'cache'
    """
    if force_tier is not None:
        if not 1 <= force_tier <= 4:
            raise ValueError(f"force_tier must be 1-4, got {force_tier}")
        return force_tier, "force"

    # Check classification cache (5-min TTL)
    task_hash = _hash_task(goal, context)
    if task_hash in _classification_cache:
        tier, timestamp = _classification_cache[task_hash]
        if time.time() - timestamp < 300:
            return tier, "cache"

    # Prune cache before adding new entry
    _prune_classification_cache()

    # Heuristic pre-filter
    tier = classify_heuristic(goal, context)
    if tier is not None:
        _classification_cache[task_hash] = (tier, time.time())
        return tier, "heuristic"

    # Router model
    tier = classify_router(goal, context)
    _classification_cache[task_hash] = (tier, time.time())
    return tier, "router"


# ── Config Management ───────────────────────────────────────────────────────

def _hermes_config_get(key: str) -> str:
    """Read a Hermes config value. Returns empty string on failure."""
    try:
        result = subprocess.run(
            ["hermes", "config", "get", key],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
        print(f"[escalation-ladder] ERROR: hermes config get {key} failed: "
              f"{result.stderr.strip()[:100]}", file=sys.stderr)
        return ""
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        print(f"[escalation-ladder] ERROR: hermes config get {key} raised {e}", file=sys.stderr)
        return ""


def _hermes_config_set(key: str, value: str) -> bool:
    """Set a Hermes config value. Returns True on success."""
    try:
        result = subprocess.run(
            ["hermes", "config", "set", key, value],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return True
        print(f"[escalation-ladder] ERROR: hermes config set {key}={value} failed: "
              f"{result.stderr.strip()[:100]}", file=sys.stderr)
        return False
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        print(f"[escalation-ladder] ERROR: hermes config set {key} raised {e}", file=sys.stderr)
        return False


def _snapshot_config() -> dict:
    """Capture current delegation config. Returns None for any key that fails to read."""
    model = _hermes_config_get("delegation.model")
    provider = _hermes_config_get("delegation.provider")
    base_url = _hermes_config_get("delegation.base_url")
    return {"model": model, "provider": provider, "base_url": base_url}


def _apply_config(config: dict) -> bool:
    """Apply a config dict (provider, base_url, model). Returns True if all three succeeded."""
    ok = True
    if not _hermes_config_set("delegation.provider", config["provider"]):
        ok = False
    if not _hermes_config_set("delegation.base_url", config["base_url"]):
        ok = False
    if not _hermes_config_set("delegation.model", config["model"]):
        ok = False
    return ok


def set_tier(tier: int, baseline: dict | None = None) -> dict:
    """
    Set delegation config for the given tier. Returns the BASELINE config for restore.

    If baseline is provided (from escalate_delegate), it is threaded through unchanged.
    Otherwise, a fresh snapshot is taken — use this ONLY for the first dispatch in a chain.
    """
    config = TIER_CONFIG[tier]

    # Use provided baseline or snapshot fresh
    if baseline is None:
        baseline = _snapshot_config()

    # Guard against poisoned snapshot (empty values from failed config reads)
    if not baseline["model"] or not baseline["provider"]:
        print("[escalation-ladder] ERROR: Baseline config snapshot failed — "
              "refusing to dispatch (would corrupt config on restore)", file=sys.stderr)
        raise RuntimeError("Cannot dispatch: baseline config snapshot is empty. "
                           "Check 'hermes config' output.")

    # Apply new config
    if not _apply_config(config):
        print("[escalation-ladder] ERROR: Failed to apply tier config — "
              "config may be in inconsistent state", file=sys.stderr)

    print(f"[escalation-ladder] INFO: Tier {tier} → {config['model']} ({config['provider']})",
          file=sys.stderr)
    return baseline


def set_frontier_fallback(baseline: dict) -> dict:
    """Set delegation to the frontier fallback model. Threads baseline through."""
    if not _apply_config(FRONTIER_FALLBACK):
        print("[escalation-ladder] ERROR: Failed to apply frontier fallback config",
              file=sys.stderr)
    print(f"[escalation-ladder] INFO: Frontier fallback → {FRONTIER_FALLBACK['model']}",
          file=sys.stderr)
    return baseline


def restore_config(baseline: dict) -> None:
    """Restore delegation config to baseline values. Skips empty keys (poison guard)."""
    restored = {}
    for key in ("model", "provider", "base_url"):
        if baseline.get(key):
            restored[key] = baseline[key]
        else:
            print(f"[escalation-ladder] ERROR: Skipping restore of empty '{key}' — "
                  f"baseline was poisoned, config may be stale", file=sys.stderr)

    if restored:
        _apply_config(restored)
        print(f"[escalation-ladder] DEBUG: Restored config → {restored.get('model', 'unknown')}",
              file=sys.stderr)


# ── Failure Cache ────────────────────────────────────────────────────────────

CACHE_DIR = Path(os.environ.get("HOME", "/tmp")) / ".hermes/skills/escalation-ladder"
CACHE_FILE = CACHE_DIR / "failure_cache.json"
MAX_ATTEMPTS = 4
CACHE_TTL = 86400  # 24 hours
MAX_CACHE_ENTRIES = 1000  # prevent unbounded disk growth


def _hash_task(goal: str, context: str) -> str:
    """SHA-256 hash of task identity."""
    return hashlib.sha256(f"{goal}{context[:200]}".encode()).hexdigest()[:16]


class FailureCache:
    """Persistent failure cache with 24h TTL and atomic writes."""

    def __init__(self):
        self._ensure_dir()

    def _ensure_dir(self):
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
        except FileExistsError:
            # Path exists but is a file, not a directory
            print(f"[escalation-ladder] ERROR: {CACHE_DIR} exists as a file, not a directory",
                  file=sys.stderr)
            raise

    def _read(self) -> dict:
        if not CACHE_FILE.exists():
            return {}
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # Corrupt cache — rename and start fresh
            corrupt_path = CACHE_FILE.with_suffix(".json.corrupt")
            try:
                CACHE_FILE.rename(corrupt_path)
                print(f"[escalation-ladder] WARNING: Corrupt cache renamed to {corrupt_path}",
                      file=sys.stderr)
            except OSError as e:
                print(f"[escalation-ladder] ERROR: Failed to rename corrupt cache: {e}",
                      file=sys.stderr)
            return {}

    def _write(self, data: dict):
        """Atomic write via temp file + rename. Enforces max entry cap."""
        # Enforce max entries (keep newest by timestamp)
        if len(data) > MAX_CACHE_ENTRIES:
            sorted_entries = sorted(
                data.items(),
                key=lambda x: x[1].get("timestamp", 0),
                reverse=True
            )
            data = dict(sorted_entries[:MAX_CACHE_ENTRIES])
            print(f"[escalation-ladder] DEBUG: Trimmed cache to {MAX_CACHE_ENTRIES} entries",
                  file=sys.stderr)

        tmp = CACHE_FILE.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.rename(CACHE_FILE)
        except OSError as e:
            print(f"[escalation-ladder] ERROR: Cache write failed: {e}", file=sys.stderr)
            # Clean up temp file
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def get(self, task_hash: str) -> dict | None:
        """Get failure record for a task hash. Returns None if not found or expired."""
        data = self._read()
        entry = data.get(task_hash)
        if entry is None:
            return None
        # Check TTL
        if time.time() - entry.get("timestamp", 0) > CACHE_TTL:
            return None
        return entry

    def record_failure(self, task_hash: str, tier: int, error: str):
        """Record a task failure at the given tier."""
        data = self._read()
        existing = data.get(task_hash, {"failures": 0})
        data[task_hash] = {
            "failures": existing.get("failures", 0) + 1,
            "last_tier": tier,
            "last_error": str(error)[:200],
            "timestamp": time.time(),
            "terminal": tier >= 4,
        }
        self._write(data)
        print(f"[escalation-ladder] WARNING: Task {task_hash} failed at Tier {tier} "
              f"(attempt {data[task_hash]['failures']}/{MAX_ATTEMPTS})", file=sys.stderr)

    def prune(self):
        """Remove expired entries."""
        data = self._read()
        now = time.time()
        pruned = {k: v for k, v in data.items() if now - v.get("timestamp", 0) <= CACHE_TTL}
        if len(pruned) < len(data):
            self._write(pruned)
            print(f"[escalation-ladder] DEBUG: Pruned {len(data) - len(pruned)} expired entries",
                  file=sys.stderr)


# ── Main Dispatch Logic ──────────────────────────────────────────────────────

def escalate_delegate(goal: str, context: str, force_tier: int | None = None) -> dict:
    """
    Full escalation dispatch pipeline.

    Captures the user's baseline config ONCE and threads it through all
    subsequent escalation steps so restore always lands on the original.

    Returns a dict with keys:
        tier, model, provider, base_url, method, baseline
    """
    # Validate inputs
    if not goal or not isinstance(goal, str):
        raise ValueError("goal must be a non-empty string")
    if not isinstance(context, str):
        raise ValueError("context must be a string")

    # Capture baseline ONCE — never re-snapshot mid-chain
    baseline = _snapshot_config()
    if not baseline["model"] or not baseline["provider"]:
        raise RuntimeError("Cannot dispatch: baseline config snapshot is empty. "
                           "Check 'hermes config' output.")

    # Classify
    tier, method = classify(goal, context, force_tier)

    # Set config (threads baseline through)
    set_tier(tier, baseline=baseline)

    return {
        "tier": tier,
        "model": TIER_CONFIG[tier]["model"],
        "provider": TIER_CONFIG[tier]["provider"],
        "base_url": TIER_CONFIG[tier]["base_url"],
        "method": method,
        "baseline": baseline,
    }


def handle_failure(goal: str, context: str, tier: int, error: str,
                   baseline: dict) -> dict | None:
    """
    Handle a task failure. Returns next dispatch info or None if terminal.

    Threads the baseline through so restore always lands on the user's original config.
    """
    cache = FailureCache()
    task_hash = _hash_task(goal, context)
    cache.record_failure(task_hash, tier, error)

    next_tier = tier + 1
    if next_tier > 4:
        print(f"[escalation-ladder] ERROR: Task {task_hash} exhausted all tiers. "
              f"Returning error to orchestrator.", file=sys.stderr)
        return None

    # Set config for next tier (threads baseline through)
    set_tier(next_tier, baseline=baseline)

    return {
        "tier": next_tier,
        "model": TIER_CONFIG[next_tier]["model"],
        "provider": TIER_CONFIG[next_tier]["provider"],
        "base_url": TIER_CONFIG[next_tier]["base_url"],
        "method": "escalation",
        "baseline": baseline,
    }


def handle_infrastructure_failure(tier: int, baseline: dict) -> dict | None:
    """
    Handle infrastructure failure (503, rate limit) at the current tier.
    For Tier 4, falls back to frontier fallback model instead of nonexistent Tier 5.
    Threads baseline through.
    """
    if tier < 4:
        # Escalate to next tier
        set_tier(tier + 1, baseline=baseline)
        return {
            "tier": tier + 1,
            "model": TIER_CONFIG[tier + 1]["model"],
            "provider": TIER_CONFIG[tier + 1]["provider"],
            "base_url": TIER_CONFIG[tier + 1]["base_url"],
            "method": "infra_fallback",
            "baseline": baseline,
        }
    else:
        # Tier 4 — try frontier fallback
        set_frontier_fallback(baseline)
        return {
            "tier": 4,
            "model": FRONTIER_FALLBACK["model"],
            "provider": FRONTIER_FALLBACK["provider"],
            "base_url": FRONTIER_FALLBACK["base_url"],
            "method": "frontier_fallback",
            "baseline": baseline,
        }


# ── CLI Entry Point ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Escalation Ladder classifier")
    parser.add_argument("goal", help="Task goal")
    parser.add_argument("--context", default="", help="Task context")
    parser.add_argument("--force-tier", type=int, choices=[1, 2, 3, 4],
                        help="Force a specific tier (skip classification)")
    parser.add_argument("--prune-cache", action="store_true",
                        help="Prune expired failure cache entries")
    args = parser.parse_args()

    if args.prune_cache:
        FailureCache().prune()
        print("Cache pruned.")
        sys.exit(0)

    tier, method = classify(args.goal, args.context, args.force_tier)
    config = TIER_CONFIG[tier]
    print(json.dumps({
        "tier": tier,
        "method": method,
        "model": config["model"],
        "provider": config["provider"],
    }))
