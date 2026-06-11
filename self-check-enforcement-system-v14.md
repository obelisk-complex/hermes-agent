# Self-Check Enforcement System — Complete Specification

## What This Is

A multi-layer enforcement system that **mechanically prevents** the Hermes agent from reporting tasks as complete when subagent validation gates have actually failed. Built through 3 major versions (v1→v3), now at **v3.4** with FAIL-pattern false-positive filtering, on_output retry-loop fix, and fully idempotent source patches.

## Architecture (4 Layers)

```
                    ┌─────────────────────────────┐
Layer 1 — Advisory  │  SOUL.md                    │  Tells agent to load harness
                    │  (always-injected identity)  │  Voluntary compliance only
                    └──────────┬──────────────────┘
                               │
                    ┌──────────▼──────────────────┐
Layer 2 — Protocol  │  self-checking-harness skill│  5-gate validation protocol
                    │                              │  Confidence scoring, evidence req
                    └──────────┬──────────────────┘
                               │
                    ┌──────────▼──────────────────┐
Layer 3 — Plugin    │  self-check-enforcer plugin │  Mechanical hook enforcement
                    │  8 hooks on 8 lifecycle pts  │  subagent_stop → detect FAIL
                    │                              │  post_tool_call → detect FAIL elsewhere
                    │                              │  pre_llm_call → inject reminder
                    │                              │  pre_tool_call → block send_message
                    │                              │  transform_tool_result → annotate
                    │                              │  on_session_start → auto-load
                    │                              │  on_session_end → cleanup state
                    │                              │  on_output → block text
                    └──────────┬──────────────────┘
                               │
                    ┌──────────▼──────────────────┐
Layer 4 — Source    │  on_output hook + NO-OP     │  CLOSES TEXT-OUTPUT BYPASS
                    │  guard                        │  Blocks ALL CLEAR text; subagent
                    │  conversation_loop.py         │  prompt rejects no-op dispatches
                    │  delegate_tool.py             │  
                    │  plugin.py → VALID_HOOKS      │  mechanically below parent's reach
                    └──────────┬──────────────────┘
                               │
                    ┌──────────▼──────────────────┐
Persistence         │  4-layer patch survival     │  git hook + in-update apply +
                    │                              │  plugin session-start + daily cron
                    └─────────────────────────────┘
```

Every subagent gets all 4 layers. The harness auto-loads on session start. Gate violations are detected on `delegate_task` return, reinforced every turn via `pre_llm_call`, blocked on tool-call escape via `pre_tool_call`, and caught on direct-text-output escape via `on_output`.

> **Path convention throughout this document:** `<hermes_root>` refers to the
> Hermes agent repository root (e.g., `~/.hermes/hermes-agent/` on a local install).
> User config paths use `~/.hermes/` (your Hermes home directory). All path
> references in instructions use these relative conventions — never hardcoded
> absolute paths.

---

## Layer 1 — SOUL.md (Advisory Enforcement)

**File:** `~/.hermes/SOUL.md`

This file is injected into every Hermes session regardless of cwd or reset. It instructs the agent to load the harness before each task.

### Contents

```markdown
## Self-checking harness
**Pre-flight:** load self-checking-harness skill before each task. Info complete? rollback path? tools+access OK? known-good state before change? can outcome be proven?
**Post-action:** actual state matches config? previously-working still works? new errors? docs updated? temps cleaned?
```

This is advisory only — the agent can ignore it. Layer 2 (protocol) and Layer 3 (plugin) provide the mechanical teeth.

---

## Layer 2 — Self-Checking Harness Skill (Protocol)

**Skill name:** `self-checking-harness`
**Location:** `~/.hermes/skills/software-development/self-checking-harness/SKILL.md`
**References:** 15 reference files under the skill's `references/` directory

### Core Protocol (5 Gates)

Every subagent must complete these gates before returning:

| Gate | What it requires | Confidence score |
|------|-----------------|-----------------|
| **Gate 1 — Evidence** | Show specific files read/written, test output, command results, source URLs. Cite exact line numbers, exit codes, or diff fragments. | — |
| **Gate 2 — Confidence Score** | Assign 0.0–1.0. 1.0 = verified by execution. 0.8 = cross-referenced sources. 0.6 = single authoritative source. 0.4 = plausible but unverified background knowledge. | Required |
| **Gate 3 — Contradiction Check** | List any evidence that contradicts or qualifies the conclusion. "None found" is valid. | — |
| **Gate 4 — Alternative Explanation** | What else could explain the evidence? Why was it rejected? | — |
| **Gate 5 — Confidence Threshold** | If score < 0.7, specify what evidence would raise it. Escalate if cannot reach 0.7. | ≥ 0.7 |

### Return Format

```json
{
  "result": "...",
  "evidence": ["file:line" or "url" or "command:output"],
  "confidence": 0.0-1.0,
  "contradictions": "... or None found",
  "alternatives_considered": "...",
  "escalation_reason": null or "..."
}
```

### Pre-Harness Allowed Tools

When the self-check enforcer plugin is active, the following tools work **before** the harness is loaded:
`read_file`, `search_files`, `web_search`, `web_extract`, `skill_view`, `skills_list`, `skill_manage`, `memory`, `session_search`, `clarify`

All other tools (`write_file`, `terminal`, `send_message`, `delegate_task`, `patch`, etc.) are blocked until the subagent calls `skill_view(name='self-checking-harness')`.

### Subagent Task Context Template

Every `delegate_task` call MUST include:

```
GOAL: [what to accomplish]

CONTEXT:
[task-specific context]

FIRST STEP: skill_view(name='self-checking-harness')

=== VALIDATION GATES (MANDATORY) ===
[copy the 5-gate protocol]

TOOLSETS: [terminal, file, web, ...]
```

### Key Rules Enforced in the Skill

- **Delegate-task decomposition:** Tasks >5 lines code, multi-step reasoning, config changes, network effects MUST use subagent with gates
- **Re-verification after fixes:** The subagent that found bugs is NOT the one that verifies the fix — use a different agent (preferably higher-reasoning model)
- **Set-of-possible-values discipline:** Every literal value in generated code/config must belong to an explicit, bounded set
- **Confidence thresholding:** Never forward subagent output with confidence < 0.7
- **No self-talk in output:** Never describe process/methodology/reasoning in the final output
- **Pre-flight planning:** Scan source diversity, flag narratives, identify contradictions, pin claims needing validation
- **Source provenance:** Every cited story must have ownership tag on first mention
- **Content review vs code review:** Self-check is for CODE tasks. Content tasks (news reports, analysis) need a SEPARATE reviewer subagent
- **Source every factual claim:** Prices, stats, dates, entities must have linked URLs
- **No fabrication:** Unverifiable entities must not be asserted
- **Explicit retraction:** When wrong, state the correction and whether it was fabrication, estimate, or reasoning error

### Reference Files

| File | Purpose |
|------|---------|
| `references/gate-enforcement-plugin.md` | Full on_output hook architecture, hook map, Opus audit findings |
| `references/plugin-enforcement.md` | Plugin structure, installation, comparison to Claude Code hooks |
| `references/patch-persistence.md` | 4-layer persistence system for source patches |
| `references/soul-anchoring.md` | SOUL.md anchoring pattern and layering |
| `references/breaking-news-watchdog.md` | Architecture for 15-min no_agent breaking news watchdog |
| `references/cifs-bandwidth-throttling.md` | Throttling batch I/O over network mounts |
| `references/available-models.md` | Query patterns for available model caches |
| `references/market-event-investigation.md` | BTC market DB investigation pattern |
| `references/matrix-cron-backfill.md` | Manual re-delivery of failed cron output |
| `references/wiki-search.md` | Wiki section-header search pattern |
| `references/dotenv-multiline-pem.md` | PEM key multiline .env fix |
| `references/factual-claim-verification.md` | Verification workflow with claim taxonomy |
| `references/competitor-research.md` | Competitor research with parallel verification |
| `references/self-correction-protocol.md` | Protocol for correcting earlier errors |
| `references/refactor-gotcha-checklist.md` | 7-item post-refactor regression checklist |

---

## Layer 3 — Self-Check Enforcer Plugin (Mechanical Enforcement)

### Plugin Metadata

**File:** `~/.hermes/plugins/self-check-enforcer/plugin.yaml`

```yaml
name: self-check-enforcer
version: "3.4.0"
description: "Auto-loads the self-checking-harness skill and enforces gate
  compliance. v3.3: subagent_stop-based delegate_task detection,
  verifies_task mechanical auto-clear, [GATE:ACCEPTING:] allowlist token,
  citation checker."
author: hermes
kind: standalone
hooks:
  - pre_tool_call
  - post_tool_call
  - pre_llm_call
  - transform_tool_result
  - subagent_stop
  - on_session_start
  - on_session_end
  - on_output
```

### Plugin Source Code

**File:** `~/.hermes/plugins/self-check-enforcer/__init__.py`

```python
"""Self-check enforcer plugin — v3.4: FAIL regex negative lookahead + opus QA fixes.

v1: auto-loads harness on session start
v2: gate-violation detection + per-turn enforcement
v3: on_output hook + session-scoped state + regex fixes
v3.1: task_id-keyed violations, LRU cap, kwarg name fix
v3.2: subagent_stop hook for delegate_task detection (avoids #12922 debate)
v3.3: verifies_task mechanical clear path, [GATE:ACCEPTING:] allowlist token,
      citation checker for file:line refs in agent output
v3.4: FAIL regex uses (?!.*(?i:\bfixed\b)) negative lookahead to suppress
      false positives from "FAIL #1 — FIXED" patterns; on_output retry loop
      fixed (continue targets while-loop via _blocked flag, not inner for-loop);
      post-loop hook guarded with not _blocked to prevent double-fire
"""

from __future__ import annotations

import collections
import json
import os
import re
import subprocess
import threading
import time
from typing import Any

# ── v1: harness auto-load state ────────────────────────────────────────
_HARNESS_LOADED: bool = False
_LOCK = threading.Lock()

# ── v3: session-scoped gate enforcement state ──────────────────────────
_MAX_SESSION_STATES = 1000
_session_states: dict[str, dict] = collections.OrderedDict()
_SESSION_LOCK = threading.Lock()


def _get_state(session_id: str) -> dict:
    """Get or create state dict for a session_id (LRU-capped at 1000)."""
    sid = str(session_id) if session_id else "_default"
    with _SESSION_LOCK:
        if sid not in _session_states:
            while len(_session_states) >= _MAX_SESSION_STATES:
                _session_states.popitem(last=False)
            _session_states[sid] = {
                "pending_gate_violation": False,
                "last_violation_detail": "",
                "violations": {},  # child_session_id -> detail lines
                "_citation_issues": [],  # [{file:line}] from citation check
                "_audit_log": [],  # [(violation_id, action, timestamp)]
            }
        else:
            _session_states.move_to_end(sid)
        return _session_states[sid]


def _cleanup_session(session_id: str) -> None:
    sid = str(session_id) if session_id else "_default"
    with _SESSION_LOCK:
        _session_states.pop(sid, None)


# ── Module-level compiled patterns ─────────────────────────────────────

_FAIL_PATTERN = re.compile(r"\bFAIL\b(?!.*(?i:\bfixed\b))")

_FAIL_PATTERN_SHORT = re.compile(r"\bFAIL\b(?!.*(?i:\bfixed\b))")

# v3.3: extract verifies_task=<uuid> from delegate_task context
_VERIFIES_TASK_RE = re.compile(r"\bverifies_task[=:]\s*(\S+)")

# v3.3: extract file:line citations from agent output
_CITATION_RE = re.compile(
    r"(?:^|[\s\"'(\[])([\w./-]+\.py):(\d+)(?=[\s\"')\].:;,!?]|$)"
)


# ── v1/v2/v3 hooks ────────────────────────────────────────────────────

def on_session_start(**_: object) -> None:
    global _HARNESS_LOADED
    with _LOCK:
        _HARNESS_LOADED = True
    _marker = os.path.expanduser("~/.hermes/patches/.patches-applied")
    if not os.path.isfile(_marker):
        _script = os.path.expanduser(
            "~/.hermes/patches/apply-on-output-patches.sh"
        )
        if os.access(_script, os.X_OK):
            threading.Thread(
                target=lambda: subprocess.run(
                    [_script], capture_output=True
                ),
                daemon=True,
            ).start()


def on_session_end(**kwargs: object) -> None:
    sid = kwargs.get("session_id", "")
    if sid:
        _cleanup_session(sid)


def on_pre_tool_call(
    *,
    tool_name: str = "",
    args: object = None,
    **kwargs: object,
) -> dict | None:
    """Block send_message if gate violation is open.

    v3.3: verifies_task is handled via subagent summary echo
    (the NO-OP guard in delegate_tool.py adds a mandatory
    instruction for the subagent to echo it back).
    """
    global _HARNESS_LOADED

    if tool_name == "skill_view" and isinstance(args, dict):
        if args.get("name") == "self-checking-harness":
            with _LOCK:
                _HARNESS_LOADED = True
            return None

    sid = kwargs.get("session_id", "")
    state = _get_state(sid)
    if (
        tool_name == "send_message"
        and state["pending_gate_violation"]
        and isinstance(args, dict)
    ):
        message = args.get("message", "")
        if _claims_all_clear(message) and not _has_clearance_token(
            message, state
        ):
            return {
                "action": "block",
                "message": (
                    "GATE VIOLATION: The last delegate_task "
                    "returned FAIL results.\n"
                    "You cannot report completion.\n\n"
                    "You must either:\n"
                    "1. Re-dispatch with verifies_task=<child_session_id> "
                    "to auto-clear, OR\n"
                    "2. Acknowledge each FAIL with "
                    "[GATE:ACCEPTING:<child_session_id>] "
                    "in your response.\n\n"
                    f"Pending:\n{state['last_violation_detail']}"
                ),
            }

    return None


# ── v3.2: subagent_stop hook (echo-based verifies_task) ───────────────

def on_subagent_stop(**kwargs: object) -> None:
    """Detect FAIL in subagent results; handle verifies_task auto-clear.

    Kwargs (confirmed via `grep -n '"subagent_stop"' tools/delegate_tool.py`
    → line 2344 on the installed commit):
      parent_session_id, child_session_id, child_summary, child_status.

    child_status: completed, failed, error, timeout, interrupted.
    """
    sid = kwargs.get("parent_session_id", "") or ""
    if not sid:
        return
    child_id = kwargs.get("child_session_id", "") or ""
    summary = kwargs.get("child_summary")
    status = kwargs.get("child_status")

    state = _get_state(sid)

    # ── v3.3: verifies_task via subagent summary echo ──────────────
    # The subagent is instructed (via 004 source patch) to echo
    # VERIFIES_TASK: <id> if context contains verifies_task=<id>.
    # Per-child echo → batch-safe, no cross-hook session-key issue.
    #
    # Trust boundary: the echo is trusted because the id is a capability
    # — the subagent can only echo an id it was given in its context.
    # If ids ever become guessable or visible across children, this
    # assumption breaks (same reasoning as clearance-token allowlist).
    verifies_original_id: str | None = None
    if status == "completed" and isinstance(summary, str):
        for line in summary.split("\n"):
            m = re.search(r"\bVERIFIES_TASK:\s*(\S+)", line)
            if m:
                verifies_original_id = m.group(1)
                break
    if verifies_original_id and verifies_original_id in state["violations"]:
        state["_audit_log"].append(
            (verifies_original_id, "VERIFIED_CLEAR",
             time.strftime("%Y-%m-%dT%H:%M:%S"))
        )
        del state["violations"][verifies_original_id]
        state["pending_gate_violation"] = len(state["violations"]) > 0
        state["last_violation_detail"] = (
            "\n---\n".join(state["violations"].values())
            if state["violations"] else ""
        )

    # ── FAIL detection (unchanged from v3.2) ──────────────────────
    if status in {"failed", "error", "timeout", "interrupted"}:
        if not summary or not isinstance(summary, str):
            state["violations"][child_id or "_no_child_id"] = (
                f"[STATUS={status}] Child exited with "
                f"failure status (no summary)"
            )
            state["pending_gate_violation"] = True
            state["last_violation_detail"] = "\n".join(
                state["violations"].values()
            )
            return

    if not summary or not isinstance(summary, str):
        return

    summary_text: str = summary
    try:
        parsed = json.loads(summary)
        if isinstance(parsed, dict):
            summary_text = json.dumps(
                parsed.get("results", parsed), indent=2
            )
        elif isinstance(parsed, list):
            summaries: list[str] = []
            for item in parsed:
                if isinstance(item, dict):
                    summaries.append(item.get("summary", str(item)))
            summary_text = "\n".join(summaries)
    except (json.JSONDecodeError, TypeError):
        pass

    has_failure_status = status in {"failed", "error", "timeout", "interrupted"}

    if _FAIL_PATTERN.search(summary_text) or has_failure_status:
        fail_lines: list[str] = []
        for line in summary_text.split("\n"):
            if _FAIL_PATTERN.search(line):
                fail_lines.append(line.strip())
        if not fail_lines and has_failure_status:
            fail_lines.append(
                f"[STATUS={status}] Child failed "
                f"(no FAIL tokens in summary)"
            )
        state["violations"][child_id or "_no_child_id"] = "\n".join(
            fail_lines[:10]
        )

    state["pending_gate_violation"] = len(state["violations"]) > 0
    state["last_violation_detail"] = "\n---\n".join(
        state["violations"].values()
    )


# ── v2/v3: other enforcement hooks ────────────────────────────────────

def on_post_tool_call(
    *,
    tool_name: str = "",
    result: object = None,
    **kwargs: object,
) -> None:
    if tool_name == "delegate_task":
        return
    if not result or not isinstance(result, str):
        return
    sid = kwargs.get("session_id", "")
    state = _get_state(sid)
    if _FAIL_PATTERN.search(result):
        state["violations"][f"tool:{tool_name}"] = (
            f"FAIL in non-delegate tool {tool_name}"
        )
        state["pending_gate_violation"] = True
        state["last_violation_detail"] = "\n---\n".join(
            state["violations"].values()
        )


def on_pre_llm_call(
    *,
    session_id: str = "",
    **_: object,
) -> dict | str | None:
    state = _get_state(session_id)
    context_parts: list[str] = []

    if state["pending_gate_violation"]:
        context_parts.append(
            "⚠️ GATE VIOLATION REMINDER\n\n"
            "The last delegate_task subagent returned FAIL results.\n"
            "You CANNOT report completion.\n\n"
            "You must either:\n"
            "1. Re-dispatch with verifies_task=<child_session_id>.\n"
            "2. Acknowledge each FAIL with "
            "[GATE:ACCEPTING:<child_session_id>].\n\n"
            f"Pending:\n{state['last_violation_detail']}\n\n"
            "send_message is blocked while this is open."
        )

    citation_issues = state.get("_citation_issues", [])
    if citation_issues:
        context_parts.append(
            "⚠️ CITATION WARNING: The following file references "
            "could not be verified:\n"
            + "\n".join(f"  • {i}" for i in citation_issues[:5])
            + "\nPlease correct or remove these references."
        )

    if not context_parts:
        return None
    return {"context": "\n\n".join(context_parts)}


def on_transform_tool_result(
    *,
    tool_name: str = "",
    result: str = "",
    **_: object,
) -> str | None:
    if tool_name != "delegate_task" or not result:
        return None
    if _FAIL_PATTERN_SHORT.search(result):
        return result + (
            "\n\n[GATE CHECK: This subagent result contains FAIL "
            "patterns. Re-dispatch or document each failure "
            "before reporting completion.]"
        )
    return None


# ── v3.3: citation checker (single pass in on_output) ─────────────────

def _discover_hermes_root() -> str:
    try:
        import hermes_constants
        root = os.path.dirname(os.path.abspath(hermes_constants.__file__))
        if os.path.isdir(os.path.join(root, "hermes_cli")):
            return root
    except ImportError:
        pass
    for candidate in (
        "/usr/local/lib/hermes-agent",
        os.path.expanduser("~/.hermes/hermes-agent"),
    ):
        if os.path.isdir(os.path.join(candidate, "hermes_cli")):
            return candidate
    return "/usr/local/lib/hermes-agent"

_HERMES_ROOT = _discover_hermes_root()


def _verify_citations(text: str) -> list[str]:
    """file:line citations → file-exists + lineno-in-range check.

    Known limitations (documented in design note 21):
    - Cannot catch wrong function names (no :line format)
    - Cannot catch wrong line numbers on real files (only guards > total)
    """
    if not text:
        return []
    issues: list[str] = []
    seen: set[str] = set()
    home = os.path.expanduser("~")
    hermes_home = os.path.join(home, ".hermes")

    for match in _CITATION_RE.finditer(text):
        filepath = match.group(1)
        lineno = int(match.group(2))
        key = f"{filepath}:{lineno}"
        if key in seen:
            continue
        seen.add(key)

        candidates: list[str] = []
        if filepath.startswith("/"):
            candidates.append(filepath)
        candidates += [
            os.path.join(_HERMES_ROOT, filepath),
            os.path.join(home, filepath),
            os.path.join(hermes_home, filepath),
        ]

        resolved: str | None = None
        for c in candidates:
            if c and os.path.isfile(c):
                resolved = c
                break

        if resolved:
            try:
                with open(resolved, errors="replace") as f:
                    total = sum(1 for _ in f)
                if lineno > total:
                    issues.append(
                        f"{filepath}:{lineno} — line {lineno} exceeds "
                        f"file ({total} lines)"
                    )
            except OSError:
                issues.append(f"{filepath}:{lineno} — could not read file")
        else:
            if filepath.endswith(".py") or "/" in filepath:
                issues.append(f"{filepath}:{lineno} — file not found")

    return issues


# ── v3/v3.3: on_output hook ───────────────────────────────────────────

def on_output(
    *,
    response_text: str = "",
    **kwargs: object,
) -> dict | None:
    sid = kwargs.get("session_id", "")
    state = _get_state(sid)

    if not response_text or response_text.strip() == "(empty)":
        return None

    # Citation check — single pass here (not in post_llm_call)
    citation_issues = _verify_citations(response_text)
    state["_citation_issues"] = citation_issues

    if state["pending_gate_violation"]:
        if _claims_all_clear(response_text) and not _has_clearance_token(
            response_text, state
        ):
            msg = (
                "⚠️ GATE VIOLATION: you attempted to report completion "
                "while unaddressed FAIL results exist.\n\n"
                "You must either:\n"
                "1. Re-dispatch with verifies_task=<child_session_id>, "
                "OR\n"
                "2. Acknowledge each FAIL with "
                "[GATE:ACCEPTING:<child_session_id>].\n\n"
                f"Pending:\n{state['last_violation_detail']}"
            )
            if citation_issues:
                msg += (
                    "\n\n⚠️ CITATION ISSUES:\n"
                    + "\n".join(f"  • {i}" for i in citation_issues[:5])
                )
            return {"action": "block", "message": msg}

        # Clearance token found — log audit trail, remove acknowledged
        if _claims_all_clear(response_text) and _has_clearance_token(
            response_text, state
        ):
            _log_acceptances(response_text, state)

    return None


# ── helpers ────────────────────────────────────────────────────────────

def _claims_all_clear(message: str) -> bool:
    patterns = [
        r"\bALL\s*CLEAR\b",
        r"\ball\s*checked?\s*(out|green|ok|good)\b",
        r"\b(?:no|zero|0)\s+(?:issues?|problems?|errors?|fails?)"
        r"\b(?!\s+remain\b)",
        r"\beverything\s*(is|looks|feels|seems(\s+to\s+be)?|appears)?"
        r"\s*(fine|good|ok|okay|working|clean|green)\b",
        r"\b(all|every)\s*(test|check|gate|finding).*pass",
        r"\bresolved?\s*(all|every)\s*(issues?|problems?|fails?)\b",
    ]
    msg_lower = message.lower()
    return any(re.search(p, msg_lower, re.IGNORECASE) for p in patterns)


def _has_clearance_token(message: str, state: dict) -> bool:
    if not state.get("violations"):
        return True
    msg_lower = message.lower()
    for vid in state["violations"]:
        vid_escaped = re.escape(vid)
        if not re.search(
            rf"\[GATE:ACCEPTING:\s*{vid_escaped}\s*\]|"
            rf"\[GATE:CLEARED:\s*{vid_escaped}\s*\]",
            msg_lower, re.IGNORECASE,
        ):
            return False
    return True


def _log_acceptances(message: str, state: dict) -> None:
    """Log [GATE:ACCEPTING:<id>] / [GATE:CLEARED:<id>] to audit trail,
    then remove acknowledged violations.

    Without this log, the clearance token is no better than the denylist
    it replaced — agent emits token, nothing records it.
    """
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    msg_lower = message.lower()
    to_remove: list[str] = []
    for vid in list(state.get("violations", {})):
        vid_escaped = re.escape(vid)
        if re.search(
            rf"\[GATE:ACCEPTING:\s*{vid_escaped}\s*\]",
            msg_lower, re.IGNORECASE,
        ):
            to_remove.append(vid)
            state["_audit_log"].append((vid, "ACCEPTED", now))
        elif re.search(
            rf"\[GATE:CLEARED:\s*{vid_escaped}\s*\]",
            msg_lower, re.IGNORECASE,
        ):
            to_remove.append(vid)
            state["_audit_log"].append((vid, "CLEARED", now))
    for vid in to_remove:
        state["violations"].pop(vid, None)
    state["pending_gate_violation"] = len(state["violations"]) > 0
    state["last_violation_detail"] = (
        "\n---\n".join(state["violations"].values())
        if state["violations"] else ""
    )


# ── registration ──────────────────────────────────────────────────────

def register(ctx) -> None:
    """Register all lifecycle and tool-call hooks.

    NOTE: post_llm_call is NOT registered — citation checker runs
    single-pass in on_output (see round 6 feedback).
    """
    ctx.register_hook("pre_tool_call", on_pre_tool_call)
    ctx.register_hook("on_session_start", on_session_start)
    ctx.register_hook("subagent_stop", on_subagent_stop)
    ctx.register_hook("post_tool_call", on_post_tool_call)
    ctx.register_hook("pre_llm_call", on_pre_llm_call)
    ctx.register_hook("transform_tool_result", on_transform_tool_result)
    ctx.register_hook("on_output", on_output)
    ctx.register_hook("on_session_end", on_session_end)
```
|### Detection Flow (Complete Cycle)

```
0. Parent calls delegate_task with verifies_task=<original_violation_id> in context
   → delegate_tool.py builds prompt: [goal] + [MANDATORY INSTRUCTION] +
     [VERIFIES_TASK INSTRUCTION] — subagent told to echo verifies_task id
   → Subagent runs, includes VERIFIES_TASK: <id> at top of summary if marker present

1. subagent_stop fires (tools/delegate_tool.py:2344)
   → Kwargs: parent_session_id, child_session_id="child-abc",
     child_summary="...", child_status="completed"
   → v3.3: Parse VERIFIES_TASK: <id> from child_summary.
     child_status="completed" AND id matches open violation?
     → Auto-clear that violation (runtime-verified, works in batch mode
       because each subagent echoes its own marker independently)
   → _FAIL_PATTERN matches? Store new violation keyed by "child-abc"
   → Flag stays: child_session_id is unique per dispatch, so no
     auto-clear on subsequent clean dispatches

2. pre_tool_call fires (every tool):
   → tool == send_message AND flag set AND message is ALL CLEAR?
     → Check _has_clearance_token(message, state) for [GATE:ACCEPTING:] bypass
     → If no clearance token → BLOCK with violation details
   → else: pass through

3. pre_llm_call fires (every turn, before LLM call):
   → flag set? Inject violation reminder (including verifies_task option)
   → citation_issues in state? Inject citation warning

4. transform_tool_result fires (registry tools only):
   → FAIL patterns found? Append [GATE CHECK] annotation

5. on_output fires (final text, no tool calls):
   → v3.3: Verify citations in final response text (single pass, not in post_llm_call)
   → flag set AND text is ALL CLEAR AND no clearance token?
     → BLOCK: inject block message (includes citation issues if any)
   → flag set AND text is ALL CLEAR AND clearance token found?
     → _log_acceptances(): log to audit trail, remove acknowledged violations
   → 5 successive blocks? Deliver abort message

7. on_session_end fires:
   → Clean up session state from _session_states dict

8. Flag persists until:
   (a) verifies_task re-dispatch succeeds → auto-clears matching violation
   (b) agent explicitly acknowledges each FAIL with [GATE:ACCEPTING:<id>]
   (c) session ends → auto-cleaned
```

### Which Bypass Paths Are Closed

| Bypass attempt | Catcher | Mechanical? |
|----------------|---------|-------------|
| `send_message("ALL CLEAR ✓")` to another channel | `pre_tool_call` blocks (v3.3: checks clearance token first) | ✅ |
| Direct text output claiming success (same channel) | `on_output` blocks, forces retry (v3.3: accepts `[GATE:ACCEPTING:]` bypass) | ✅ |
| LLM keeps producing violating text (5+ times) | `on_output` retry limit → abort | ✅ |
| Budget-exhaustion summary claims success | `on_output` budget path → replaces text | ✅ |
| Agent ignores SOUL.md, doesn't load harness | `on_session_start` auto-loads | ✅ |
| Subagent doesn't call skill_view | `pre_tool_call` blocks write/terminal/delegate_task | ✅ |
| Agent dispatches no-op subagent to clear gate flag | `delegate_tool.py` injects MANDATORY INSTRUCTION: FAIL on no-op work | ✅ Mechanical — below agent's reach |
| Agent dispatches unrelated clean task to clear flag | `subagent_stop` keyed on `child_session_id` — new UUID per dispatch, no auto-clear | ✅ (v3.2) Structural — opaque UUID, no matching possible |
| Agent dispatches same task to clear flag (with verifies_task) | `verifies_task` auto-clear — but child must genuinely return `child_status="completed"` from runtime | ✅ (v3.3) Mechanical — runtime-verified status |
| Agent fabricates file/line citations in output | `on_output` verifies `.py:line` refs against filesystem (file-exists + lineno-in-range only) | ⚠️ (v3.3) Advisory — catches nonexistent files and lines-past-EOF, NOT wrong function names or wrong line numbers on real files (see design note 21) |
| Agent uses non-standard completion language | Regex denylist v3: (still evadable but false positives fixed) | ⚠️ Advisory — pattern boundary |
| Agent ignores pre_llm_call reminder | Not sufficient alone — but on_output catches text | ✅ (chained) |
| Agent writes completion without [GATE:ACCEPTING:] token | `_has_clearance_token()` requires explicit token per open violation | ✅ (v3.3) Structural — bounded allowlist |

---

## Layer 4 — Source Modifications (on_output Hook + NO-OP Guard)

### Why It's Needed

The `pre_tool_call` hook only intercepts tool CALLS. If the agent outputs success text as a natural-language response (no tool call), the hook doesn't fire. `pre_llm_call` injects a reminder but can't block the text — compliance relies on the agent's integrity.

The `on_output` hook is a **custom extension** to the Hermes agent's plugin system that fires **inside the conversation loop**, before the turn is committed. A plugin can return `{"action": "block", "message": "..."}` to inject a rejection message as a user turn and `continue` — the model retries with corrected context.

**Additionally**, the NO-OP rejection guard closes the "dispatch a fake subagent to clear the flag" bypass by mechanically injecting a FAIL instruction into every subagent's system prompt at the tool-handler level — below the parent agent's reach.

### Source Changes Required

#### Change 1 — Register the hook name

**File:** `hermes_cli/plugins.py` — add `"on_output"` to `VALID_HOOKS` set

```python
    # on_output — fires when the LLM finishes its final text response (no tool
    # calls).  Plugins return a dict {"action": "block", "message": "..."}
    # to reject the output and force the model to retry.  Return None to allow.
    # Kwargs: response_text, session_id, model, platform
    "on_output",
```

Without this, `ctx.register_hook("on_output", ...)` logs a warning but the hook is never called.

#### Change 2 — Initialise retry counter

**File:** `agent/conversation_loop.py` — in retry initialisation block (~line 450)

```python
    agent._thinking_prefill_retries = 0
    agent._post_tool_empty_retried = False
    agent._on_output_blocks = 0          # <-- ADD THIS
    _blocked = False                     # <-- ADD THIS (output plugin block flag)
    agent._last_content_with_tools = None
```

Resets at the start of every `run_conversation()` call (each user turn).

#### Change 3 — Hook call site: main text response

**File:** `agent/conversation_loop.py` — inside the no-tool-call branch,
**after** `messages.append(final_msg)` (~line 4449) and **before**
`_turn_exit_reason = "text_response(...)"` (~line 4485).

```python
                # ── Plugin hook: on_output ──────────────────────────────────
                # Fires when the LLM produces final text with no tool calls.
                # Plugin returns {"action": "block", "message": "..."} to
                # reject the output and force the model to retry.
                _blocked = False
                if final_response and not interrupted:
                    from hermes_cli.plugins import invoke_hook as _on_invoke
                    _on_results = _on_invoke(
                        "on_output",
                        response_text=final_response,
                        session_id=agent.session_id or "",
                        model=agent.model,
                        platform=getattr(agent, "platform", None) or "",
                    )
                    for _ores in _on_results:
                        if isinstance(_ores, dict) and _ores.get("action") == "block":
                            _msg = _ores.get(
                                "message",
                                "Output rejected by policy. "
                                "Please revise and retry.",
                            )
                            messages.append({"role": "user", "content": _msg})
                            agent._empty_content_retries = 0
                            agent._post_tool_empty_retried = False
                            agent._on_output_blocks += 1
                            _blocked = True
                            break
                    if _blocked:
                        if agent._on_output_blocks > 4:
                            final_response = (
                                "\u26a0\ufe0f Output blocked after "
                                "5 attempts. The task could not be "
                                "completed due to repeated policy "
                                "violations."
                            )
                            agent._on_output_blocks = 0
                        else:
                            continue  # Retry: continue outer while loop

                if _blocked:
                    _turn_exit_reason = "text_response(blocked_by_policy)"
                else:
                    _turn_exit_reason = f"text_response(finish_reason={finish_reason})"
```

**Behaviour:**
- No block → falls through to normal `break`
- Block, attempts ≤ 4 → injects block message as user turn, `continue` targets outer while loop (real LLM retry)
- Block, attempts = 5 → abort message delivered, loop breaks

#### Change 4 — Hook call site: budget exhaustion path

**File:** `agent/conversation_loop.py` — after `_handle_max_iterations()` (~line 4564)

```python
        # Fire on_output for budget-exhaustion summary text too.
        # Guard with not _blocked so normal text completions (which already
        # fired the in-loop hook) skip this second invocation.
        if final_response and not interrupted and not _blocked:
            from hermes_cli.plugins import invoke_hook as _budget_invoke
            _budget_results = _budget_invoke(
                "on_output",
                response_text=final_response,
                session_id=agent.session_id or "",
                model=agent.model,
                platform=getattr(agent, "platform", None) or "",
            )
            for _bres in _budget_results:
                if isinstance(_bres, dict) and _bres.get("action") == "block":
                    _msg = _bres.get(
                        "message",
                        "Output rejected by policy during "
                        "budget-exhaustion summary.",
                    )
                    final_response = _msg
                    break
```

No retry here — iteration budget is exhausted. The block message replaces the summary text.

#### Change 5 — Plugin registers on_output callback

See Layer 3 plugin source above (`on_output()` handler and `register()` calls `ctx.register_hook("on_output", on_output)`).

#### Change 6 — NO-OP rejection guard in subagent prompt

**File:** `tools/delegate_tool.py` — `_build_child_system_prompt()` function

The subagent system prompt is built by assembling parts around the user-provided `goal` and `context`. A new block is mechanically appended **after** the workspace path and **before** the "Complete this task" instructions:

```python
    # ── NO-OP rejection guard ─────────────────────────────────────────────
    # Mechanically injected into every subagent prompt. If the parent agent
    # dispatched a no-op task (e.g. to clear gate flags), the subagent must
    # FAIL explicitly rather than fabricate a clean result.
    parts.append(
        "\\n\\n═══ MANDATORY INSTRUCTION ═══\\n"
        "If this task requires no real work, or the work has already\\n"
        "been completed by a prior subagent, return FAIL with reason\\n"
        "'NO-OP REJECTION: <explanation>'. Do NOT fabricate results.\\n"
        "A FAIL return is preferred over a fake-clean result.\\n"
        "═══ END MANDATORY INSTRUCTION ═══"
    )
```

The parent agent cannot omit or suppress this instruction — it is injected at the tool handler level (`delegate_tool.py`), below the agent's control. When the subagent returns `FAIL with 'NO-OP REJECTION: ...'`, the existing `post_tool_call` → FAIL detection chain catches it just like any real gate violation.

**Patch file:** `004-on-output-delegate-task-noop-guard.patch`

### on_output Hook Contract

| Aspect | Detail |
|--------|--------|
| Hook name | `on_output` |
| Registration | `ctx.register_hook("on_output", my_handler)` |
| Callback kwargs | `response_text: str, session_id: str, model: str, platform: str, **kwargs` |
| Return None | Allow output (normal delivery) |
| Return dict | `{"action": "block", "message": "your rejection text"}` |
| Return other | Treated as None (allowed) |
| Multiple callbacks | First block dict wins; all collected but consumed on first hit |

### ALL CLEAR Detection Regex + Allowlist Token

```python
def _claims_all_clear(message: str) -> bool:
    patterns = [
        r"\bALL\s*CLEAR\b",
        r"\ball\s*checked?\s*(out|green|ok|good)\b",
        r"\b(?:no|zero|0)\s+(?:issues?|problems?|errors?|fails?)"
        r"\b(?!\s+remain\b)",
        r"\beverything\s*(is|looks|feels|seems(\s+to\s+be)?|appears)?"
        r"\s*(fine|good|ok|okay|working|clean|green)\b",
        r"\b(all|every)\s*(test|check|gate|finding).*pass",
        r"\bresolved?\s*(all|every)\s*(issues?|problems?|fails?)\b",
    ]
    msg_lower = message.lower()
    return any(re.search(p, msg_lower, re.IGNORECASE) for p in patterns)

def _has_clearance_token(message: str, state: dict) -> bool:
    """v3.3: Check for [GATE:ACCEPTING:<id>] for all open violations."""
    if not state.get("violations"):
        return True
    msg_lower = message.lower()
    for vid in state["violations"]:
        vid_escaped = re.escape(vid)
        if not re.search(
            rf"\[GATE:ACCEPTING:\s*{vid_escaped}\s*\]|"
            rf"\[GATE:CLEARED:\s*{vid_escaped}\s*\]",
            msg_lower, re.IGNORECASE,
        ):
            return False
    return True
```

**Denylist** (belt-and-suspenders): Covers `ALL CLEAR`, `all checked out/good`, `no issues/problems/errors`, `everything is/looks/feels/seems/appears fine/good/ok/working/clean/green`, `all tests/checks/gates PASS`, `resolved all problems`.

**Allowlist token** (v3.3, load-bearing): Output containing `[GATE:ACCEPTING:<child_session_id>]` for every open violation bypasses the denylist. The agent cannot forge this token without either (a) re-running the failing task (verifies_task auto-clears) or (b) explicitly acknowledging the failure in writing.

From one design principle made two: the denylist blocks known-bad phrasings (leaky); the allowlist requires one known-good signal (bounded).

**Critical nuance:** The original `everything\s*(is\s*)?(fine|good|ok|working|clean)` pattern missed `everything looks good` (because `looks` ≠ `is`). Fixed by expanding verb list.

### Timing Nuance: on_output vs transform_llm_output

```
on_output fires here (in-loop, can continue/retry)
    ↓
break from while loop
    ↓
transform_llm_output fires here (post-loop, text morph only)
    ↓
post_llm_call fires
```

`transform_llm_output` can replace `final_response` but cannot re-enter the loop. `on_output` fires inside the loop where `continue` is still meaningful.

### Edge Cases Closed by Opus Audit

1. **Budget exhaustion bypass** — on_output now fires after `_handle_max_iterations` too. No retry possible but block message replaces violating summary.
2. **Guardrail halt bypass** — guardrail halts set final_response in the tool-call branch, not the no-tool-call branch. Acceptable: guardrail text is system-generated.
3. **Empty response bypass** — `"(empty)"` sentinel paths break before reaching on_output. Acceptable: not an ALL CLEAR claim.
4. **Retry limit** — raised from 3 to 5 after Opus review (too tight for real scenarios).

---

## Persistence System — Surviving Hermes Auto-Updates

`hermes update` does `git reset --hard origin/main` → `uv pip install -e .` which destroys local source modifications. Four redundant layers keep the on-output patches applied.

### Layer 1 — Git Post-Merge Hook

**File:** `<hermes_root>/.git/hooks/post-merge`

```bash
#!/usr/bin/env bash
HOOK_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$HOOK_DIR" || exit 1

APPLY_SCRIPT="$HOME/.hermes/patches/apply-on-output-patches.sh"
if [ -x "$APPLY_SCRIPT" ]; then
    exec "$APPLY_SCRIPT"
fi
```

Fires after every successful `git pull` (first step of `hermes update`). Runs BEFORE the in-update apply (Layer 2), so patches are restored before the update process continues.

### Layer 2 — In-Update Apply (Most Reliable)

**In `hermes_cli/main.py`** (`_cmd_update_impl` function, after `print("✓ Code updated!")`):

```python
        # Re-apply custom on-output patches after update overwrites source
        try:
            import subprocess, os
            _apply_script = os.path.expanduser(
                "~/.hermes/patches/apply-on-output-patches.sh"
            )
            if os.access(_apply_script, os.X_OK):
                subprocess.run([_apply_script], check=False)
        except Exception:
            pass  # Non-fatal — patches can be applied manually
```

This creates a **self-healing chain**: the third patch (which adds this apply call to `main.py`) is re-applied by the post-merge hook (Layer 1) which runs earlier. Since `main.py` now has the call, every future `hermes update` automatically re-applies all patches.

### Layer 3 — Plugin Session-Start Safety Net (File Marker)

In the plugin's `on_session_start` handler (see Layer 3 source).
Uses a file marker (`~/.hermes/patches/.patches-applied`) instead of
importing the Hermes module — O(stat) vs O(module load) per session:

```python
    # Quick file-marker check for patch persistence (O(stat), not O(import)).
    # The apply script touches .patches-applied on success.
    _marker = os.path.expanduser(
        "~/.hermes/patches/.patches-applied"
    )
    if not os.path.isfile(_marker):
        _script = os.path.expanduser(
            "~/.hermes/patches/apply-on-output-patches.sh"
        )
        if os.access(_script, os.X_OK):
            threading.Thread(
                target=lambda: subprocess.run(
                    [_script], capture_output=True
                ),
                daemon=True,
            ).start()
```

Catches cases where neither git hooks nor the in-update apply ran (manual `pip install -e .`, fresh clone, or marker file deleted).

### Layer 4 — Daily Cron Watchdog

```
Job ID: 6142bee89e29
Schedule: 0 6 * * * (daily at 06:00)
Script: verify-on-output-patches.py
Delivery: local (saves results, silent when OK)
No agent: true
```

**File:** `~/.hermes/patches/verify-on-output-patches.py`

```python
#!/usr/bin/env python3
"""Verify on-output patches are applied. Exit 0 if OK, 1 if missing."""
import sys
import os

# Check 1: VALID_HOOKS
sys.path.insert(0, '<hermes_root>')
try:
    from hermes_cli.plugins import VALID_HOOKS
    if 'on_output' not in VALID_HOOKS:
        print("MISSING: on_output not in VALID_HOOKS")
        sys.exit(1)
except ImportError as e:
    print(f"ERROR: {e}")
    sys.exit(1)

# Check 2: conversation_loop has the hook call site
loop_path = '<hermes_root>/agent/conversation_loop.py'
if os.path.isfile(loop_path):
    with open(loop_path) as f:
        content = f.read()
    if '"on_output"' not in content:
        print("MISSING: on_output hook call in conversation_loop.py")
        sys.exit(1)

# Check 3: main.py has the post-update apply call
main_path = '<hermes_root>/hermes_cli/main.py'
if os.path.isfile(main_path):
    with open(main_path) as f:
        content = f.read()
    if 'apply-on-output-patches' not in content:
        print("MISSING: post-update apply call in main.py")
        sys.exit(1)

# Check 4: delegate_tool.py has the NO-OP guard
delegate_path = '<hermes_root>/tools/delegate_tool.py'
if os.path.isfile(delegate_path):
    with open(delegate_path) as f:
        content = f.read()
    if 'NO-OP REJECTION' not in content:
        print("MISSING: NO-OP rejection guard in tools/delegate_tool.py")
        sys.exit(1)

print("OK: all on-output patches applied")
sys.exit(0)
```

### Patch Files (Git Diffs)

**Live files on disk:** `~/.hermes/patches/` (4 `.patch` files)
**Note:** The diff snapshots below show the patches as originally authored. The on-disk versions reflect the latest fixes (e.g., `_blocked` flag retry loop, removed redundant `import os`). Run `apply-on-output-patches.sh` to verify current state.

**Location:** `~/.hermes/patches/`

| File | Applies to | What it does |
|-----|-----------|-------------|
| `001-on-output-valid-hooks.patch` | `hermes_cli/plugins.py` | Adds `"on_output"` to `VALID_HOOKS` |
| `002-on-output-conversation-loop.patch` | `agent/conversation_loop.py` | Adds hook call site + retry logic |
| `003-on-output-update-hook.patch` | `hermes_cli/main.py` | Calls apply script after update |
| `004-on-output-delegate-task-noop-guard.patch` | `tools/delegate_tool.py` | Injects FAIL-on-no-op in every subagent prompt |

Plus these helper scripts:

| File | Purpose |
|-----|--------|
| `apply-on-output-patches.sh` | Applies all 4 patches via `git apply`, touches `.patches-applied` marker |
| `verify-on-output-patches.py` | Checks all 4 patches, exit 0 if OK |

#### `001-on-output-valid-hooks.patch` — plugins.py VALID_HOOKS

```diff
diff --git a/hermes_cli/plugins.py b/hermes_cli/plugins.py
index fd449fc27..1bb963081 100644
--- a/hermes_cli/plugins.py
+++ b/hermes_cli/plugins.py
@@ -134,6 +134,11 @@ VALID_HOOKS: Set[str] = {
     # Plugins return a string to replace the response text, or None/empty.
     # First non-None string wins.
     "transform_llm_output",
+    # on_output — fires when LLM finishes final text (no tool calls).
+    #  Plugins return {"action": "block", "message": "..."}
+    #  to reject output and force retry.  Return None to allow.
+    #  Kwargs: response_text, session_id, model, platform
+    "on_output",
     "pre_llm_call",
     "post_llm_call",
     "pre_api_request",
```

#### `002-on-output-conversation-loop.patch` — 2 hook call sites

```diff
diff --git a/agent/conversation_loop.py b/agent/conversation_loop.py
index d01d5d4a8..a6fcc8896 100644
--- a/agent/conversation_loop.py
+++ b/agent/conversation_loop.py
@@ -448,6 +448,7 @@ def run_conversation(
     agent._codex_incomplete_retries = 0
     agent._thinking_prefill_retries = 0
     agent._post_tool_empty_retried = False
+    agent._on_output_blocks = 0
     agent._last_content_with_tools = None
     agent._last_content_tools_all_housekeeping = False
     agent._mute_post_response = False
@@ -4446,7 +4447,41 @@ def run_conversation(
                     messages.pop()
 
                 messages.append(final_msg)
-                
+
+                # ── Plugin hook: on_output ──────────────────────────────────
+                if final_response and not interrupted:
+                    from hermes_cli.plugins import invoke_hook as _on_invoke
+                    _on_results = _on_invoke(
+                        "on_output",
+                        response_text=final_response,
+                        session_id=agent.session_id or "",
+                        model=agent.model,
+                        platform=getattr(agent, "platform", None) or "",
+                    )
+                    for _ores in _on_results:
+                        if isinstance(_ores, dict) and _ores.get("action") == "block":
+                            _msg = _ores.get(
+                                "message",
+                                "Output rejected by policy. Please revise.",
+                            )
+                            messages.append({"role": "user", "content": _msg})
+                            agent._empty_content_retries = 0
+                            agent._post_tool_empty_retried = False
+                            agent._on_output_blocks += 1
+                            if agent._on_output_blocks > 4:
+                                final_response = (
+                                    "\u26a0\ufe0f Output blocked after 5 attempts."
+                                )
+                                agent._on_output_blocks = 0
+                                break
+                            continue
+
                 _turn_exit_reason = f"text_response(finish_reason={finish_reason})"
                 if not agent.quiet_mode:
                     agent._safe_print("🎉 Conversation completed after ...")
@@ -4528,6 +4563,27 @@ def run_conversation(
             )
         final_response = agent._handle_max_iterations(messages, api_call_count)
 
+        # Fire on_output for budget-exhaustion summary text too.
+        if final_response and not interrupted:
+            from hermes_cli.plugins import invoke_hook as _budget_invoke
+            _budget_results = _budget_invoke(
+                "on_output",
+                response_text=final_response,
+                session_id=agent.session_id or "",
+                model=agent.model,
+                platform=getattr(agent, "platform", None) or "",
+            )
+            for _bres in _budget_results:
+                if isinstance(_bres, dict) and _bres.get("action") == "block":
+                    _msg = _bres.get(
+                        "message",
+                        "Output rejected by policy during budget-exhaustion.",
+                    )
+                    final_response = _msg
+                    break
+
         # If running as a kanban worker, signal the dispatcher...
```

#### `003-on-output-update-hook.patch` — main.py post-update apply

```diff
diff --git a/hermes_cli/main.py b/hermes_cli/main.py
index 391d85f1b..156042c1e 100644
--- a/hermes_cli/main.py
+++ b/hermes_cli/main.py
@@ -10662,6 +10662,15 @@ def _cmd_update_impl(args, gateway_mode: bool):
         print()
         print("✓ Code updated!")
 
+        # Re-apply custom on-output patches after update overwrites source
+        try:
+            _apply_script = os.path.expanduser(
+                "~/.hermes/patches/apply-on-output-patches.sh"
+            )
+            if os.access(_apply_script, os.X_OK):
+                subprocess.run([_apply_script], check=False)
+        except Exception:
+            pass  # Non-fatal — patches can be applied manually
+
         # After git pull, source files on disk are newer than cached...
```

### Apply Script

**File:** `~/.hermes/patches/apply-on-output-patches.sh`

```bash
#!/usr/bin/env bash
# Apply on-output hook patches to Hermes agent source.
# Called automatically after `hermes update` via git post-merge hook.
# Also safe to run manually any time.
set -euo pipefail

HERMES_ROOT="<hermes_root>"
PATCH_DIR="$HOME/.hermes/patches"

# Determine which apply method is available
APPLY=""
if command -v git &>/dev/null && [ -d "$HERMES_ROOT/.git" ]; then
    APPLY="git -C $HERMES_ROOT apply"
elif command -v patch &>/dev/null; then
    APPLY="patch -p1 -d $HERMES_ROOT"
else
    echo "✗ Neither git nor patch available — cannot apply patches"
    exit 1
fi

apply_patch() {
    local patch_file="$1"
    local name="$2"

    if [ ! -f "$patch_file" ]; then
        echo "  ⚠ Patch not found: $patch_file (skipping)"
        return 0
    fi

    # Check if already applied (dry-run check — succeeds = not yet applied)
    if $APPLY --check "$patch_file" 2>/dev/null; then
        echo "  → Applying: $name..."
        $APPLY "$patch_file"
        echo "  ✓ Applied: $name"
    else
        # Reverse check — succeeds = already applied
        if $APPLY --reverse --check "$patch_file" 2>/dev/null; then
            echo "  ✓ Already applied: $name"
        else
            echo "  ⚠ Cannot apply: $name (conflict — manual fix needed)"
            echo "    Patch: $patch_file"
            return 1
        fi
    fi
}

echo "on-output hook: applying source patches..."
apply_patch "$PATCH_DIR/001-on-output-valid-hooks.patch" "VALID_HOOKS registration"
apply_patch "$PATCH_DIR/002-on-output-conversation-loop.patch" "conversation_loop hook"
apply_patch "$PATCH_DIR/003-on-output-update-hook.patch" "main.py post-update apply hook"
apply_patch "$PATCH_DIR/004-on-output-delegate-task-noop-guard.patch" "delegate_task NO-OP guard"
echo "Done."
touch "$PATCH_DIR/.patches-applied"
```

> **Note — this 4-layer treadmill is contingent on upstream adoption.** If the `on_output` hook is implemented natively in the Hermes agent (merged into `hermes_cli/plugins.py` as a first-class `VALID_HOOKS` entry with the call site in `conversation_loop.py`), the entire persistence system becomes dead weight. All four layers plus the 4 `.patch` files plus the git hook plus the daily cron watchdog can be deleted: the native hook fires without source patches, survives `hermes update` natively, and needs no re-apply logic. Until that happens, the treadmill stays.

---

## File Layout — Complete Directory Structure

```
~/.hermes/
├── SOUL.md                                              # Layer 1: advisory
├── plugins/
│   └── self-check-enforcer/
│       ├── plugin.yaml                                  # Layer 3: metadata
│       └── __init__.py                                  # Layer 3: source
├── patches/                                             # Layer 4 persistence
│   ├── 001-on-output-valid-hooks.patch
│   ├── 002-on-output-conversation-loop.patch
│   ├── 003-on-output-update-hook.patch
│   ├── 004-on-output-delegate-task-noop-guard.patch
│   ├── apply-on-output-patches.sh
│   ├── verify-on-output-patches.py
│   └── .patches-applied                                 # Marker: touched on successful apply
├── skills/
│   └── software-development/
│       └── self-checking-harness/
│           ├── SKILL.md                                 # Layer 2: protocol
│           └── references/
│               ├── gate-enforcement-plugin.md
│               ├── plugin-enforcement.md
│               ├── patch-persistence.md
│               ├── soul-anchoring.md
│               ├── breaking-news-watchdog.md
│               ├── cifs-bandwidth-throttling.md
│               ├── available-models.md
│               ├── market-event-investigation.md
│               ├── matrix-cron-backfill.md
│               ├── wiki-search.md
│               ├── dotenv-multiline-pem.md
│               ├── factual-claim-verification.md
│               ├── competitor-research.md
│               ├── self-correction-protocol.md
│               └── refactor-gotcha-checklist.md
├── scripts/
│   └── test-on-output-hook.py                           # Integration test
│
<hermes_root>/                             # Hermes repo
├── hermes_cli/
│   ├── plugins.py                                       # Patched: VALID_HOOKS
│   └── main.py                                          # Patched: post-update apply
├── agent/
│   └── conversation_loop.py                             # Patched: on_output hook sites
├── tools/
│   └── delegate_tool.py                                 # Patched: NO-OP rejection guard
└── .git/hooks/
    └── post-merge                                       # Layer 1: post-pull apply
```

---

## Test Suite

### Plugin-Level Test

**File:** `~/.hermes/scripts/test-on-output-hook.py`

Tests the full `invoke_hook` chain for `on_output` by directly registering the enforcer plugin's callbacks with the Hermes plugin manager.

Coverage: 45+ tests across these groups:
- Hook registration (3 tests)
- Normal output without violation (1 test)
- ALL CLEAR blocked during violation (4 tests)
- Non-claiming text passes through during violation (3 tests)
- `everything looks good` variants blocked (1 test covering 7 variants)
- `(empty)` and empty-string sentinel passthrough (2 tests)
- No-violation state allows all text (2 tests)
- Comprehensive ALL CLEAR pattern coverage — 26 patterns (24 original + 2 v3 false-positive-edge cases)
- Session-scoped state isolation (2 tests)

Run with:
```bash
cd <hermes_root> && python3 ~/.hermes/scripts/test-on-output-hook.py
```

### Conversation-Loop Integration Test (in reference doc)

Simulates the `continue`/`break`/retry-limit logic from `conversation_loop.py` without a running agent. Tests: 5 successive blocks → abort, budget-exhaustion interception, clean-budget passthrough. 17 tests.

---

## First-Time Install on a New Hermes Instance

### Prerequisites

- Hermes agent installed at `<hermes_root>`
- Hermes running (editable install: `pip install -e .`)
- Python 3.10+

### Step-by-Step

```bash
# 1. Create SOUL.md
cat > ~/.hermes/SOUL.md << 'EOF'
## Self-checking harness
**Pre-flight:** load self-checking-harness skill before each task. Info complete? rollback path? tools+access OK? known-good state before change? can outcome be proven?
**Post-action:** actual state matches config? previously-working still works? new errors? docs updated? temps cleaned?
EOF

# 2. Create plugin directory + files
mkdir -p ~/.hermes/plugins/self-check-enforcer

# Write plugin.yaml (see Layer 3 section)
# Write __init__.py (see Layer 3 source code — full 255-line plugin)

# 3. Create patch directory + files
mkdir -p ~/.hermes/patches

# Write all 4 .patch files (from Layer 4 Persistence section)
# Write apply-on-output-patches.sh
# Write verify-on-output-patches.py
chmod +x ~/.hermes/patches/apply-on-output-patches.sh

# 4. Apply patches to Hermes source
bash ~/.hermes/patches/apply-on-output-patches.sh

# 5. Install git post-merge hook
cp ~/.hermes/patches/apply-on-output-patches.sh \
   <hermes_root>/.git/hooks/post-merge
chmod +x <hermes_root>/.git/hooks/post-merge

# 6. Create verify script + cron
cp ~/.hermes/patches/verify-on-output-patches.py ~/.hermes/scripts/
chmod +x ~/.hermes/scripts/verify-on-output-patches.py

# 7. Schedule daily cron
# Use cronjob tool with:
#   schedule: "0 6 * * *"
#   no_agent: true
#   script: verify-on-output-patches.py
#   deliver: local

# 8. Verify
python3 ~/.hermes/patches/verify-on-output-patches.py
# Should say: OK: all on-output patches applied

# 9. Restart Hermes
# For CLI: restart the agent
# For gateway: restart gateway process

# 10. Run test suite
cd <hermes_root> && python3 ~/.hermes/scripts/test-on-output-hook.py
# Should say: 45 passed, 0 failed
```

### Verification Checklist

After install, confirm:

- [ ] SOUL.md exists and contains self-checking-harness instruction
- [ ] Plugin directory exists with `plugin.yaml` and `__init__.py`
- [ ] Plugin registers all 8 hooks: `pre_tool_call`, `post_tool_call`, `pre_llm_call`, `transform_tool_result`, `subagent_stop`, `on_session_start`, `on_session_end`, `on_output`
- [ ] Patches directory has 6 files (4 .patch, apply.sh, verify.py)
- [ ] `.patches-applied` marker file exists (created by apply script)
- [ ] `"on_output" in VALID_HOOKS` returns True (run `verify-on-output-patches.py`)
- [ ] `agent/conversation_loop.py` contains `on_output` hook call sites (2 locations)
- [ ] `hermes_cli/main.py` contains `apply-on-output-patches` call
- [ ] `tools/delegate_tool.py` contains `NO-OP REJECTION` guard
- [ ] `.git/hooks/post-merge` exists, executable, calls apply script
- [ ] Cron job exists: schedule `0 6 * * *`, script `verify-on-output-patches.py`
- [ ] Plugin test suite passes (45+ tests)
- [ ] New Hermes session: any subagent session auto-starts with harness loaded (no `skill_view` needed)
- [ ] delegate_task returning FAIL sets `_pending_gate_violation` flag
- [ ] `send_message("ALL CLEAR")` blocked when violation open
- [ ] `pre_llm_call` injects violation reminder into every turn while flag set
- [ ] Direct text claiming success blocked by `on_output` hook during violation
- [ ] 5 successive blocks deliver abort message
- [ ] Unrelated clean delegate_task does NOT clear the flag (goal-scoped — v3)
- [ ] Session A violation does not affect session B (session isolation — v3)

---

## Regenerating Patches After Upstream Merges

If an upstream Hermes update touches the same files, patches may conflict. Regenerate:

```bash
cd <hermes_root>
# Manually re-apply the intended changes (Changes 1-6 from Layer 4), then:
git diff HEAD -- hermes_cli/plugins.py \
  > ~/.hermes/patches/001-on-output-valid-hooks.patch
git diff HEAD -- agent/conversation_loop.py \
  > ~/.hermes/patches/002-on-output-conversation-loop.patch
git diff HEAD -- hermes_cli/main.py \
  > ~/.hermes/patches/003-on-output-update-hook.patch
git diff HEAD -- tools/delegate_tool.py \
  > ~/.hermes/patches/004-on-output-delegate-task-noop-guard.patch
```

Then verify: `python3 ~/.hermes/patches/verify-on-output-patches.py`

---

## Design Notes

1. **Why source modification instead of stdout wrapper:**
   A stdout wrapper lacks a feedback loop — the model proceeds unaware output was blocked. The core hook with `continue` injection forces retry. The process-level pipe is now optional defense-in-depth.

2. **Why git apply instead of monkey-patching:**
   `git apply` handles fuzz automatically, is machine-readable, survives `git stash`/`checkout` cycles, and can be tested with `--check` / `--reverse --check`.

3. **Why 5 retries (not 3):**
   Opus audit found 3 too tight for real scenarios where the model needs a couple of attempts to correct.

4. **Why post-merge hook runs before in-update apply:**
   The `hermes update` process: `git pull` → post-merge fires (re-applies main.py patch) → `✓ Code updated!` → patched main.py calls apply script again (idempotent). The third patch (to main.py) is re-applied by the post-merge hook, which runs before the apply call is reached. Self-healing chain.

5. **Editable install (not reinstall needed):**
   `pip install -e .` means patches to tracked files take effect on next Hermes restart — no reinstall necessary.

6. **Plugin process-local state is correct:**
   Each subagent gets a fresh Python process with its own `_HARNESS_LOADED` flag. `on_session_start` fires for every new session, setting the flag. On agent restart it resets — each fresh session gets a fresh auto-load.

7. **File marker over module import for patch check (E1):**
   Layer 3 originally imported `hermes_cli.plugins.VALID_HOOKS` on every session start — a full module load for every session (including subagent sessions). Replaced with `os.path.isfile(~/.hermes/patches/.patches-applied)` — a filesystem stat that costs ~0ms. The marker is touched by `apply-on-output-patches.sh` after successful apply. If missing, the background apply fires once. Remaining persistence layers (1, 2, 4) continue to provide redundancy.

8. **NO-OP guard lives in tool handler, not conversation loop:**
   The instruction is injected into `_build_child_system_prompt()` in `tools/delegate_tool.py`, not in the conversation loop. This is because the subagent's system prompt is assembled entirely inside the delegate tool handler — the conversation loop only sees the final summary. Patching the tool handler is the only place where the instruction can be mechanically inserted below the agent's control.

9. **v3 session-scoped state (P0a):** Changed from module-level globals to `dict[session_id, state]` pattern with `_SESSION_LOCK`. Eliminates cross-session contamination in gateway mode where one process serves many sessions.

10. **v3 goal-scoped violation clearing (P0b):** Violations stored keyed by the delegate_task's goal text (first 200 chars). A clean result only clears violations matching its own goal — dispatching an unrelated clean task cannot clear the flag. The parent must re-use the same goal text or explicitly document each FAIL.

11. **FAIL_PATTERN simplified to `\bFAIL\b` only (v3):** The original pattern included `\bconfidence<0.5`, `\bescalation_reason\b`, and `\bgate.*fail|violation` to catch edge cases, but every real violation path (NO-OP guard, child status fallback, plugin block messages) already produces `\bFAIL\b`. The extra alternatives caused false positives on descriptive analysis text ("confidence was 0.35 for audio segment 3") with zero additional recall. Reduced to pure `\bFAIL\b` — the only pattern that discriminative detection needs.

12. **v3 false positive fix (P1b):** Added `(?!\s+remain\b)` negative lookahead to the "no issues" pattern. "No issues remain after documenting each FAIL" (compliant output) now passes; "no issues found" (ALL CLEAR) still blocks.

13. **v3 compiled patterns:** `_FAIL_PATTERN` and `_FAIL_PATTERN_SHORT` compiled at module load, not re-compiled per call. Removed the 3 separate `re.compile()` calls in handlers. Later simplified from 4-alternative regex to pure `\bFAIL\b` — the extra alternatives provided no additional detection surface (all real violations produce FAIL) while causing false positives on descriptive text mentioning confidence thresholds.

14. **v3 on_session_end hook:** Registered to clean up session state on session end. Prevents memory leak from accumulated `_session_states` in long-lived gateway processes.

15. **v3.1 task_id-scoped violation keying:** Changed from `goal[:200]` text key to `task_id` (opaque UUID from hook payload). Eliminates false stickiness from rephrased goals and false clearing from shared goal boilerplate. The agent cannot forge a matching task_id — only explicit documentation clears the flag.

16. **v3.1 kwarg name fix:** Changed `kwargs.get("function_args")` to `kwargs.get("args", {})`. The `post_tool_call` handler previously relied on a fallback that happened to work (`kwargs.get("args")`). The `invoke_hook` in `_emit_post_tool_call_hook` (model_tools.py:842) passes `args=function_args`, not `function_args=...`.

17. **v3.1 _session_states LRU cap:** `_session_states` changed from `dict` to `collections.OrderedDict` with a 1000-entry LRU eviction policy.

18. **v3.2 subagent_stop migration:** Switched delegate_task detection from `post_tool_call` to `subagent_stop`. The `subagent_stop` hook fires in `tools/delegate_tool.py:2344`, independent of `handle_function_call` — sidesteps the #12922 debate entirely. Kwargs include `parent_session_id`, `child_session_id`, `child_summary`, `child_status` as named arguments. Keying on `child_session_id` (opaque UUID per dispatch) means no auto-clear on subsequent clean dispatches — the flag persists until explicit documentation or session end. `post_tool_call` retained for non-delegate tools only.

19. **v3.3 verifies_task auto-clear (feedback point 1, round6 fix):** The NO-OP guard in delegate_tool.py injects a mandatory instruction for the subagent to echo `VERIFIES_TASK: <id>` if the task context contains `verifies_task=<id>`. `on_subagent_stop` parses the child summary for this echo and auto-clears the matching violation when `child_status="completed"`. The echo is mechanically injected below the parent's reach — the parent cannot suppress it. This approach works in batch mode (each subagent echoes its own marker independently, unlike the v3.3.0 `pre_tool_call` capture which used a single session slot and cleared the wrong child in batch dispatches).

20. **v3.3 allowlist token inversion (feedback point 2, round6 audit fix):** Before v3.3, the system used a pure denylist of success idioms (`_claims_all_clear`) which is unbounded — every new "looks good" phrasing is another evasion vector. v3.3 inverts this: while violations are open, completion language is blocked unless the output carries `[GATE:ACCEPTING:<child_session_id>]` for every open violation. The `_has_clearance_token()` helper checks for these tokens. The denylist stays as belt-and-suspenders but is no longer load-bearing. The agent can only obtain clearance tokens by (a) re-running with verifies_task (mechanical clear) or (b) explicitly acknowledging each failure (honest override). **Round 6 addition:** `_log_acceptances()` is called when clearance tokens pass — it records each acknowledged violation to `state["_audit_log"]` with timestamp and action (ACCEPTED or CLEARED), then removes it from open violations. Without this log, the clearance token would be no better than the denylist (agent emits token, nothing records it).

21. **v3.3 citation checker (feedback point 3, round6 pragmatic fix):** The `on_output` handler runs `_verify_citations()` on the final response text, extracting `file.py:line` refs and verifying each against the filesystem via `_discover_hermes_root()` (imports `hermes_constants` at runtime, falls back to known paths). Issues are stored in `state["_citation_issues"]` and surfaced: (a) in `pre_llm_call` warnings next turn, and (b) appended to gate violation block messages when both apply. **Known limitations (round 6):** (i) Cannot catch wrong _function_ names like `_plugin_hooks.dispatch()` because the regex requires `file.py:line` format — missing either part and it doesn't match. (ii) Cannot catch wrong line numbers on real files (e.g. `delegate_tool.py:2306` when the real call is at 2344) because the check only guards `lineno > total_lines`, not whether the cited _symbol_ is at that line — a symbol-proximity grep would be needed for that, which is a materially different check. (iii) Runs in `on_output` only (single pass), not duplicated in `post_llm_call`. Honest boundary: the checker catches nonexistent files and lines-past-EOF but cannot verify semantic accuracy of citations. This is explicitly stated as a limitation rather than shipping security theatre.

 22. **v3.4 FAIL regex false-positive filter (round 8 gate fix):** `_FAIL_PATTERN` changed from `\bFAIL\b` to `\bFAIL\b(?!.*(?i:\bfixed\b))`. When a subagent summary contains "FAIL #1 - FIXED" throughout section headers, the gate scanner previously saw FAIL and flagged it as a violation -- even though every FAIL was followed by FIXED describing a remediated issue. The negative lookahead suppresses matches when FIXED appears after FAIL on the same line, case-insensitively. FAIL itself remains case-sensitive.

 23. **v3.4 on_output retry loop scope fix (round 7 QA):** The in-loop on_output's `continue` and `break` targeted the inner `for _ores` loop, not the outer `while` loop. Fixed by introducing a `_blocked` flag: for loop sets `_blocked = True` and breaks; afterwards, if blocked and retry limit not exceeded, `continue` targets the while loop triggering a real LLM retry. `_blocked` initialized at function scope so the post-loop guard can reference it.

 24. **v3.4 post-loop double-fire guard (round 7 QA):** Post-loop hook guarded with `and not _blocked` so normal text completions that already fired the in-loop hook skip the second invocation. Budget-exhaustion and error exits bypass the in-loop handler, so `_blocked` stays False and the post-loop hook fires correctly for non-standard exits.

 25. **v3.4 Source patches idempotent (round 8):** All 4 patch files in `~/.hermes/patches/` updated to match the committed-on-fork source code. `003-on-output-update-hook.patch` no longer contains `import subprocess, os` (removed the redundant local import that caused `UnboundLocalError`). `002-on-output-conversation-loop.patch` updated to reflect the `_blocked`-flag fixed version. The `apply-on-output-patches.sh` script's existing idempotency logic now reports `✓ Already applied` for all 4 patches instead of `⚠ Cannot apply (conflict)`.

 26. **v3.4 FAIL_PATTERN_SHORT kept in sync:** Updated identically to FAIL_PATTERN for consistency.

---

## Git Diff Summary (All Changes)

```
 hermes_cli/plugins.py             |  5 +++++
 agent/conversation_loop.py        | 63 +++++++++++++++++++++++++++----
 hermes_cli/main.py                | 91 ++++++++++++++++++++++++++++++++
 tools/delegate_tool.py            | 16 +++++++++
 .git/hooks/post-merge             |  9 +++++++
 ~/.hermes/plugins/self-check-enforcer/__init__.py | 2 regex lines changed
                                                      (v3.4)
 ~/.hermes/patches/                |  8 files (4 patches + 2 scripts + marker)
 ~/.hermes/scripts/test-on-output-hook.py | 20 lines changed (v3 state API)
 ~/.hermes/plugins/self-check-enforcer/plugin.yaml | +2 hooks declared
 9 files changed, ~210 insertions
```

---

## Complete Hook Map (All Hooks Used by This System)

| Hook | Fires | Return value | What this system uses it for |
|------|-------|-------------|------------------------------|
| `pre_tool_call(tool_name, kwargs)` | Before any tool executes | `dict` to block; `None` to allow | Capture `verifies_task` from delegate_task context (v3.3); block `send_message` claiming ALL CLEAR while violation open; check `[GATE:ACCEPTING:]` clearance token (v3.3) |
| `post_tool_call(tool_name, kwargs, result)` | After any tool returns | Ignored | Detect FAIL in non-delegate tool results (delegate_task handled by subagent_stop) |
| `pre_llm_call(messages)` | Once per turn, before LLM loop | `{"context": "..."}` to inject | Inject gate-violation reminder + citation warnings into every turn |
|| `post_llm_call(messages, response)` | Once per turn, after LLM | Ignored | (Not used — citation verification runs single-pass in `on_output`) |
| `transform_llm_output(response_text, ...)` | After loop exit, before delivery | First non-None string replaces `final_response` | (Not used) |
| `transform_tool_result(tool_name, result)` | After tool returns, before model sees | Modified result string | Annotate FAIL results with [GATE CHECK] marker |
| `on_session_start()` | New session created | Ignored | Auto-load harness; trigger background patch apply if marker missing |
| `on_session_end()` | End of `run_conversation` | Ignored | Clean up session-scoped state from `_session_states` dict |
| `on_output(response_text, ...)` | Final text produced, before loop break | `{"action": "block", "message": "..."}` or `None` | Block ALL CLEAR text output while violation open; accept `[GATE:ACCEPTING:]` clearance token bypass (v3.3); verify file:line citations in final text (v3.3) |
| `pre_gateway_dispatch(message)` | Gateway received user message | `{"action": "skip"/"rewrite"/"allow"}` | (Not used) |
| `subagent_stop(parent_session_id, child_session_id, child_summary, child_status)` | After each delegate_task child finishes | Ignored | Detect FAIL in child_summary via `_FAIL_PATTERN`; auto-clear matching violation on `verifies_task` re-run (v3.3) |
