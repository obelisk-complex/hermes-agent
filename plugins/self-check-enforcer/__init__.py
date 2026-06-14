"""Self-check enforcer plugin — v3.7.1: round-5 QA remediation.

v1: auto-loads harness on session start
v2: gate-violation detection + per-turn enforcement
v3: on_output hook + session-scoped state + regex fixes
v3.1: task_id-keyed violations, LRU cap, kwarg name fix
v3.2: subagent_stop hook for delegate_task detection
v3.3: verifies_task mechanical clear, clearance tokens, citation checker
v3.4: FAIL regex, read-only exemption, on_output retry fix
v3.5: GH Actions sync, CI test fixes, Layer 4 removed
v3.5.1: local auto-rebase removed
v3.5.2: on_output double-fire fix (_on_output_fired flag)
v3.5.3: goal context in violations, tighter FAIL_PATTERN_SHORT,
      post_tool_call uses SHORT pattern, skill_view exemption
v3.5.4: _FAIL_PATTERN_SHORT excludes adjacent punctuation
      (" ' ) ] }) to prevent false positives from source-code
      scanning (grep/ripgrep returning literal "FAIL" strings)
v3.6.0: round-1 QA fan-out remediation —
      - FAIL detection no longer masked by the word "fixed" on the line
        (closes a trivial gate bypass: "FAIL: x — will be fixed");
      - _FAIL_PATTERN_SHORT rewritten: dropped dead conjugation lookahead
        and the [\\s] typo; keeps punctuation exclusion; adds "FAIL to"
        natural-language exclusion;
      - clearance tokens ([GATE:ACCEPTING:<id>]) now clear the gate even
        without all-clear phrasing (honest acknowledgement path);
      - tool-keyed violations surface their [tool:<name>] clearance id and
        are clearable;
      - distinct summary-less childless failures no longer collide on one key;
      - violation reminders point at the bracketed id shown in the detail;
      - citation checker also resolves project-cwd-relative paths.
v3.7.0: verification-protocol features (bottega-inspired) —
      - READY/NEEDS_WORK/BLOCKED verdict: a child reporting verdict=BLOCKED or a
        non-null escalation_reason (even with no FAIL token) opens an escalation
        gate, labelled BLOCKED so the parent surfaces it to a human;
      - strict plan↔artifact matching: claimed file creations/writes whose
        target is absent are flagged (advisory) — "checked but not done" caught;
      - structured feedback: violation detail is an enumerated per-id checklist
        of failed checks (targeted retry list);
      - (paired with SKILL.md: verdict field + acceptance-scenarios template,
        and delegate_tool.py: mandatory run-the-scenarios instruction).
v3.7.1: round-5 QA fan-out remediation —
      - NEEDS_WORK verdict now opens a re-runnable gate on its own (a child that
        follows the SKILL.md contract no longer bypasses by reporting NEEDS_WORK
        in prose without a literal FAIL token);
      - _claims_all_clear closes done-claim bypasses ("verification complete",
        "task complete", "all findings addressed", "nothing failed");
      - dead _VERIFIES_TASK_RE removed (production uses the inline VERIFIES_TASK:
        echo path);
      - on_output block / 5-block-escalation decision extracted to
        agent/_on_output_gate.py for direct unit tests; delegate_tool.py now
        mandates the verdict field in the child prompt.
"""

from __future__ import annotations

import collections
import json
import os
import re
import threading
import time

# ── v1: harness auto-load state ────────────────────────────────────────
_HARNESS_LOADED: bool = False
_LOCK = threading.Lock()

# ── v3: session-scoped gate enforcement state ──────────────────────────
# Dict keyed by session_id.  LRU eviction at 1000 entries as backstop
# for gateway mode (on_session_end may not fire per #2817-era behaviour).
_MAX_SESSION_STATES = 1000
_session_states: dict[str, dict] = collections.OrderedDict()
_SESSION_LOCK = threading.Lock()


def _get_state(session_id: str) -> dict:
    """Get or create state dict for a session_id (LRU-capped at 1000)."""
    sid = str(session_id) if session_id else "_default"
    with _SESSION_LOCK:
        if sid not in _session_states:
            # Evict oldest if at capacity — backstop for gateway mode
            # where on_session_end may not fire reliably.
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
            # Move to end (most recently used) when accessed
            _session_states.move_to_end(sid)
        return _session_states[sid]


def _cleanup_session(session_id: str) -> None:
    """Remove state for a completed session."""
    sid = str(session_id) if session_id else "_default"
    with _SESSION_LOCK:
        _session_states.pop(sid, None)


def _fallback_child_key(state: dict) -> str:
    """Unique key for a failing child with no resolvable session id.

    Without this, multiple summary-less failures in one batch all key on the
    constant "_no_child_id" and silently overwrite each other's detail.
    """
    n = sum(1 for k in state["violations"] if str(k).startswith("_no_child_id"))
    return f"_no_child_id_{n}"


# ── Module-level compiled patterns ─────────────────────────────────────

# Detects the structured "FAIL" marker subagents emit.  We deliberately do
# NOT suppress lines that also contain "fixed": a summary such as
# "FAIL: auth bypass — will be fixed later" is an UNRESOLVED failure and must
# open the gate.  \bFAIL\b already excludes conjugations (FAILED/FAILS/...).
# Detection-first is correct for a security gate — a false positive is
# clearable by the agent; a false negative is a silent bypass.
_FAIL_PATTERN = re.compile(r"\bFAIL\b")

# Tighter pattern for non-subagent scanning (post_tool_call, transform_tool_result).
# \bFAIL\b already excludes conjugations (FAILED/FAILING/FAILURE/FAILOVER/FAILS),
# so this only adds two narrow exclusions to cut false positives:
#   (1) FAIL immediately followed by a quote/bracket — grep/ripgrep echoing a
#       literal "FAIL" string from source scanning;
#   (2) "FAIL to <verb>" — natural-language "fail to ...", not a status marker.
_FAIL_PATTERN_SHORT = re.compile(
    r"\bFAIL\b"
    r"(?![\"')}\]])"
    r"(?!\s+(?i:to)\b)"
)

# v3.3: extract file:line citations from agent output
# Matches patterns like "conversation_loop.py:450" or "path/to/file.py:123"
_CITATION_RE = re.compile(
    r"(?:^|[\s\"'(\[])([\w./-]+\.py):(\d+)(?=[\s\"')\].:;,!?]|$)"
)

# v3.7.0: BLOCKED verdict / escalation_reason detection for subagent_stop.
_BLOCKED_RE = re.compile(r'\bverdict\b["\']?\s*[:=]\s*["\']?BLOCKED\b', re.IGNORECASE)

# v3.7.1: NEEDS_WORK verdict detection. SKILL.md defines NEEDS_WORK as a
# re-runnable FAIL (any verification/test failed); it must open a gate on its
# own so a contract-following child that reports the verdict in prose — without
# also emitting a literal FAIL token — cannot silently bypass the gate.
_NEEDS_WORK_RE = re.compile(
    r'\bverdict\b["\']?\s*[:=]\s*["\']?NEEDS_WORK\b', re.IGNORECASE
)


def _blocked_reason(text: str) -> str | None:
    """Return the escalation reason if a subagent summary reports a BLOCKED
    verdict or a non-null escalation_reason, else None.

    A BLOCKED child needs a human — it opens an escalation gate distinct from a
    re-runnable FAIL. Tries the JSON-quoted escalation_reason first (so internal
    commas survive), then a plain `key: value`/`key=value` form.
    """
    if not text:
        return None
    has_verdict = bool(_BLOCKED_RE.search(text))
    reason: str | None = None
    m = re.search(r'["\']escalation_reason["\']\s*:\s*"((?:[^"\\]|\\.)*)"', text)
    if not m:
        m = re.search(r"\bescalation_reason\b\s*[:=]\s*(.+)", text)
    if m:
        r = m.group(1).strip().strip("\"'").rstrip(",}").strip()
        if r and r.lower() not in ("null", "none"):
            reason = r
    if has_verdict or reason:
        return reason or "no escalation reason provided"
    return None


# ── v1/v2/v3 hooks ────────────────────────────────────────────────────

def on_session_start(**_: object) -> None:
    """Auto-enable harness on every session start."""
    global _HARNESS_LOADED
    with _LOCK:
        _HARNESS_LOADED = True


def on_session_end(**kwargs: object) -> None:
    """Clean up session state on session end."""
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

    v3.3: verifies_task is handled via subagent summary echo (the NO-OP guard
    in delegate_tool.py adds a mandatory instruction to echo it back).
    """
    global _HARNESS_LOADED

    # Track harness loading
    if tool_name == "skill_view" and isinstance(args, dict):
        if args.get("name") == "self-checking-harness":
            with _LOCK:
                _HARNESS_LOADED = True
            return None

    # ── v2: Block send_message when violation open ─────────────────
    sid = kwargs.get("session_id", "")
    state = _get_state(sid)
    if (
        tool_name == "send_message"
        and state["pending_gate_violation"]
        and isinstance(args, dict)
    ):
        message = args.get("message", "")
        if _claims_all_clear(message) and not _has_clearance_token(message, state):
            return {
                "action": "block",
                "message": (
                    "GATE VIOLATION: a prior step returned FAIL results.\n"
                    "You cannot report completion to the user.\n\n"
                    "Each pending violation is labelled with its id in "
                    "[brackets] in the detail below. To resolve, either:\n"
                    "1. Re-dispatch the failing task with verifies_task=<id> "
                    "to auto-clear, OR\n"
                    "2. Acknowledge each one with [GATE:ACCEPTING:<id>] in "
                    "your response (honest override).\n\n"
                    f"Pending violations:\n{state['last_violation_detail']}"
                ),
            }

    return None


# ── v3.2: subagent_stop hook ──────────────────────────────────────────

def on_subagent_stop(**kwargs: object) -> None:
    """Detect FAIL in subagent results; handle verifies_task auto-clear.

    Kwargs come from the ``invoke_hook("subagent_stop", ...)`` call in
    tools/delegate_tool.py (locate by symbol — the line number drifts on
    every upstream rebase, so it is deliberately not cited here):
      parent_session_id, child_session_id, child_summary, child_status.

    child_status enum values:
      - "completed"   ← child produced a summary
      - "failed"      ← child ran but produced no summary
      - "error"       ← unhandled exception in child
      - "timeout"     ← child exceeded its time budget
      - "interrupted" ← parent cancelled child mid-exec
    Status in {"failed","error","timeout","interrupted"} -> violation.
    """
    sid = kwargs.get("parent_session_id", "") or ""
    if not sid:
        return
    child_id = kwargs.get("child_session_id", "") or ""
    summary = kwargs.get("child_summary")
    status = kwargs.get("child_status")

    state = _get_state(sid)

    # ── v3.3: verifies_task auto-clear via subagent summary echo ──
    # The NO-OP guard in delegate_tool.py injects a mandatory instruction
    # for the subagent to echo VERIFIES_TASK: <id> if the task context
    # contains verifies_task=<id>.  This works in batch mode because each
    # subagent independently echoes its own marker, and the child_session_id
    # + child_status come from the runtime, not the agent.
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
            if state["violations"]
            else ""
        )
        # Fall through — still scan this child's summary for new FAIL tokens

    # ── v3.2/v3: FAIL detection ────────────────────────────────────
    # Short-circuit: failure status with no usable summary
    if status in {"failed", "error", "timeout", "interrupted"}:
        if not summary or not isinstance(summary, str):
            key = child_id or _fallback_child_key(state)
            state["violations"][key] = (
                f"[{key}] — 1 failed check(s):\n"
                f"  ✗ [STATUS={status}] child exited with failure status "
                f"(no summary available)"
            )
            state["pending_gate_violation"] = True
            state["last_violation_detail"] = "\n".join(
                state["violations"].values()
            )
            return
        # status is failure but summary IS available — fall through

    if not summary or not isinstance(summary, str):
        return

    # Parse structured summaries (JSON batch format)
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
    blocked_reason = _blocked_reason(summary_text)  # v3.7.0: BLOCKED/escalation
    needs_work = bool(_NEEDS_WORK_RE.search(summary_text))  # v3.7.1: re-runnable

    if _FAIL_PATTERN.search(summary_text) or has_failure_status or needs_work:
        fail_lines: list[str] = []
        for line in summary_text.split("\n"):
            if _FAIL_PATTERN.search(line):
                fail_lines.append(line.strip())
        if not fail_lines and has_failure_status:
            fail_lines.append(
                f"[STATUS={status}] Child failed (no FAIL tokens in summary)"
            )
        if not fail_lines and needs_work:
            fail_lines.append(
                "[VERDICT=NEEDS_WORK] Child reported the work is incomplete "
                "(re-runnable: re-dispatch with verifies_task or acknowledge)"
            )
        key = child_id or _fallback_child_key(state)
        shown = fail_lines[:10]
        # v3.7.0: enumerated per-id checklist so the agent gets a targeted retry
        # list (which specific checks failed), not an opaque blob.
        state["violations"][key] = (
            f"[{key}] — {len(shown)} failed check(s):\n"
            + "\n".join(f"  ✗ {ln}" for ln in shown)
        )
        # If this same child was just VERIFIED_CLEAR'd above but its summary
        # still contains FAIL, record the re-open so the audit trail is not a
        # misleading lone VERIFIED_CLEAR with no matching re-open.
        if key == verifies_original_id:
            state["_audit_log"].append(
                (key, "REOPENED", time.strftime("%Y-%m-%dT%H:%M:%S"))
            )
    elif blocked_reason:
        # v3.7.0: a completed child reporting verdict=BLOCKED / an
        # escalation_reason still opens a gate — but it needs a HUMAN, so it is
        # labelled BLOCKED rather than presented as a re-runnable FAIL.
        key = child_id or _fallback_child_key(state)
        state["violations"][key] = (
            f"[{key}] — BLOCKED (escalate to a human): {blocked_reason}\n"
            f"  ✗ Do not silently mark this complete. Surface the blocker to "
            f"the user, or take responsibility with [GATE:ACCEPTING:{key}]."
        )
        # Same re-open audit symmetry as the FAIL branch: if this child was
        # just VERIFIED_CLEAR'd but came back BLOCKED, record the re-open.
        if key == verifies_original_id:
            state["_audit_log"].append(
                (key, "REOPENED", time.strftime("%Y-%m-%dT%H:%M:%S"))
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
    """Check non-delegate tool results for FAIL patterns.

    NOTE: delegate_task handled by on_subagent_stop (v3.2 migration).
    Read-only content tools are exempt — they return file/page content
    verbatim, so "FAIL" in the output is descriptive text, not a
    tool-operation failure. (v3.4)
    """
    if tool_name in ("delegate_task", "memory", "read_file", "search_files", "session_search", "web_extract", "patch", "skill_view"):
        return
    if not result or not isinstance(result, str):
        return

    sid = kwargs.get("session_id", "")
    state = _get_state(sid)

    if _FAIL_PATTERN_SHORT.search(result):
        key = f"tool:{tool_name}"
        state["violations"][key] = (
            f"[{key}]\n"
            f"FAIL detected in output of non-delegate tool '{tool_name}'. "
            f"To clear: re-run the operation cleanly, or acknowledge with "
            f"[GATE:ACCEPTING:{key}]."
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
    """Inject gate-violation + citation reminders every turn."""
    state = _get_state(session_id)

    context_parts: list[str] = []

    # Gate violation reminder
    if state["pending_gate_violation"]:
        context_parts.append(
            "⚠️ GATE VIOLATION REMINDER\n\n"
            "A prior step returned FAIL results. You CANNOT report "
            "completion.\n\n"
            "Each pending violation is labelled with its id in [brackets] "
            "in the detail below. To resolve, either:\n"
            "1. Re-dispatch the failing task with verifies_task=<id> to "
            "auto-clear.\n"
            "2. Acknowledge each one with [GATE:ACCEPTING:<id>] in your "
            "response (honest override — no all-clear wording required).\n\n"
            f"Pending:\n{state['last_violation_detail']}\n\n"
            "send_message is blocked while this is open."
        )

    # v3.3: Citation warning (feedback point 3)
    citation_issues = state.get("_citation_issues", [])
    if citation_issues:
        context_parts.append(
            "⚠️ CITATION WARNING: The following file references in "
            "your last response could not be verified:\n"
            + "\n".join(f"  • {i}" for i in citation_issues[:5])
            + "\nPlease correct or remove these references in your next output."
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
    """Annotate delegate_task results that contain FAIL patterns."""
    if tool_name != "delegate_task" or not result:
        return None

    if _FAIL_PATTERN_SHORT.search(result):
        return result + (
            "\n\n[GATE CHECK: This subagent result contains FAIL patterns. "
            "You must re-dispatch or explicitly document each failure "
            "before reporting completion.]"
        )

    return None


# ── v3.3: citation checker (feedback point 3) ─────────────────────────

def _discover_hermes_root() -> str:
    """Discover Hermes repo root at runtime.  Falls back to common paths."""
    # Try importing hermes_constants for the most reliable path
    try:
        import hermes_constants  # type: ignore
        root = os.path.dirname(os.path.abspath(hermes_constants.__file__))
        if os.path.isdir(os.path.join(root, "hermes_cli")):
            return root
    except ImportError:
        pass
    # Static fallbacks
    for candidate in (
        "/usr/local/lib/hermes-agent",
        os.path.expanduser("~/.hermes/hermes-agent"),
    ):
        if os.path.isdir(os.path.join(candidate, "hermes_cli")):
            return candidate
    # Last resort — search parent dirs from this file's location
    return "/usr/local/lib/hermes-agent"


_HERMES_ROOT = _discover_hermes_root()


def _verify_citations(text: str) -> list[str]:
    """Verify file:line citations in text against the filesystem.

    Checks existence and line range for .py file refs in agent output.
    Returns list of problematic citations with descriptions.
    Designed to catch fabricated source references like
    ``_plugin_hooks.dispatch()`` or wrong line numbers.
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

        # Candidate search paths: absolute, under Hermes root, under home
        candidates: list[str] = []
        if filepath.startswith("/"):
            candidates.append(filepath)
        candidates += [
            os.path.join(_HERMES_ROOT, filepath),
            os.path.join(os.getcwd(), filepath),
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
                with open(resolved, encoding="utf-8", errors="replace") as f:
                    total = sum(1 for _ in f)
                if lineno > total:
                    issues.append(
                        f"{filepath}:{lineno} — line {lineno} exceeds "
                        f"file ({total} lines)"
                    )
            except OSError:
                issues.append(f"{filepath}:{lineno} — could not read file")
        else:
            # Only flag if it looks like a real reference (has .py or path)
            if filepath.endswith(".py") or "/" in filepath:
                issues.append(f"{filepath}:{lineno} — file not found")

    return issues


# v3.7.0: strict plan↔artifact matching — claimed file creations/writes whose
# target does not exist on disk. Existence only (reliable); content matching
# stays advisory (the verifier agent does it — we cannot know expected content).
_CLAIMED_FILE_RE = re.compile(
    r"\b(?:created|wrote|added|generated|saved|produced)\b[^\n`]{0,40}`([\w./+-]+\.\w{1,8})`",
    re.IGNORECASE,
)


def _verify_claimed_files(text: str) -> list[str]:
    """Flag files the output CLAIMS to have created/written that are absent.

    Conservative by design: requires a completion verb adjacent to a
    backtick-quoted path with an extension, so it does not fire on prose that
    merely mentions a path. A 'done' claim with no artifact is the canonical
    "checked but not actually done" failure (bottega strict-matching).
    """
    if not text:
        return []
    issues: list[str] = []
    seen: set[str] = set()
    home = os.path.expanduser("~")
    hermes_home = os.path.join(home, ".hermes")
    for m in _CLAIMED_FILE_RE.finditer(text):
        path = m.group(1)
        if path in seen:
            continue
        seen.add(path)
        candidates = [path] if path.startswith("/") else []
        candidates += [
            os.path.join(_HERMES_ROOT, path),
            os.path.join(os.getcwd(), path),
            os.path.join(home, path),
            os.path.join(hermes_home, path),
        ]
        if not any(os.path.isfile(c) for c in candidates):
            issues.append(
                f"{path} — claimed created/written but not found on disk "
                f"(strict plan-matching: a 'done' claim without the artifact is a FAIL)"
            )
    return issues


# ── v3/v3.3: on_output hook ───────────────────────────────────────────

def on_output(
    *,
    response_text: str = "",
    **kwargs: object,
) -> dict | None:
    """Block text output claiming ALL CLEAR while gate violation is open.

    v3.3 changes:
      - Accepts [GATE:ACCEPTING:<child_session_id>] clearance tokens
        (allowlist inversion — feedback point 2).
      - Surfaces citation issues in block message when both apply
        (feedback point 3).
    """
    sid = kwargs.get("session_id", "")
    state = _get_state(sid)

    if not response_text or response_text.strip() == "(empty)":
        return None

    # ── Citation + claimed-artifact check (single pass here) ──
    citation_issues = _verify_citations(response_text)
    citation_issues = citation_issues + _verify_claimed_files(response_text)  # v3.7.0
    state["_citation_issues"] = citation_issues

    # ── Gate violation handling (allowlist inversion — feedback point 2) ──
    if state["pending_gate_violation"]:
        # Clear any violations the agent explicitly acknowledged with a
        # clearance token, regardless of surrounding phrasing.  _log_acceptances
        # self-guards (it only pops violations whose id carries a matching
        # token), so calling it unconditionally is a no-op when none is present.
        # This lets an honest "I acknowledge this failure [GATE:ACCEPTING:<id>]"
        # clear the gate WITHOUT also having to claim all-clear (fixes the
        # contradiction where the documented clearance path was a no-op).
        _log_acceptances(response_text, state)

        # Re-evaluate after clearing: if violations REMAIN and the text still
        # claims completion, block and force a retry.
        if state["pending_gate_violation"] and _claims_all_clear(response_text):
            msg = (
                "⚠️ GATE VIOLATION: you attempted to report completion "
                "while unaddressed FAIL results exist.\n\n"
                "Each pending violation is labelled with its id in [brackets] "
                "in the detail below. To resolve, either:\n"
                "1. Re-dispatch the failing task with verifies_task=<id> "
                "(auto-clears when the child completes), OR\n"
                "2. Acknowledge each one with [GATE:ACCEPTING:<id>] in your "
                "response (honest override — no all-clear wording required).\n\n"
                f"Pending:\n{state['last_violation_detail']}"
            )
            if citation_issues:
                msg += (
                    "\n\n⚠️ CITATION ISSUES:\n"
                    + "\n".join(f"  • {i}" for i in citation_issues[:5])
                )
            return {"action": "block", "message": msg}

    return None


# ── helpers ────────────────────────────────────────────────────────────

def _claims_all_clear(message: str) -> bool:
    """Detect success-claim language (denylist, belt-and-suspenders).

    This is a backstop behind the reliable clear path: the agent acknowledges
    each open violation with [GATE:ACCEPTING:<id>] (see _has_clearance_token).
    Detection-first — broad here, cheaply clearable by the token — so prefer
    catching a done-claim over missing one (a missed claim is a silent bypass).

    v3: excludes "no issues remain after..." false positive via
    negative lookahead (?!\\s+remain\\b).
    """
    patterns = [
        r"\bALL\s*CLEAR\b",
        r"\ball\s*checked?\s*(out|green|ok|good)\b",
        # v3: exclude "no issues remain after..." (false positive)
        r"\b(?:no|zero|0)\s+(?:issues?|problems?|errors?|fails?)"
        r"\b(?!\s+remain\b)",
        r"\beverything\s*(is|looks|feels|seems(\s+to\s+be)?|appears)?"
        r"\s*(fine|good|ok|okay|working|clean|green)\b",
        r"\b(all|every)\s*(test|check|gate|finding).*pass",
        r"\bresolved?\s*(all|every)\s*(issues?|problems?|fails?)\b",
        # v3.7.1: close done-claim bypasses the round-5 audit found — a completion
        # claim with an open violation must block (token is the reliable clear).
        r"\bverification\s+complete\b",
        r"\b(?:all|every)\s+findings?\s+(?:addressed|resolved|fixed|cleared)\b",
        r"\btask\s+complete\b",
        r"\bnothing\s+(?:failed|broke|went\s+wrong|is\s+broken)\b",
        r"\b(?:output|work|results?|everything)\s+verified\b",
    ]
    msg_lower = message.lower()
    return any(re.search(p, msg_lower, re.IGNORECASE) for p in patterns)


def _has_clearance_token(message: str, state: dict) -> bool:
    """Check for [GATE:ACCEPTING:<id>] or [GATE:CLEARED:<id>] for all violations.

    v3.3 (feedback point 2): Invert denylist → require an explicit allowed
    token for each open violation.  The agent can only legitimately emit
    these tokens after addressing the failure (re-run with verifies_task,
    which auto-clears, or explicit acknowledgement).
    """
    if not state.get("violations"):
        return True
    msg_lower = message.lower()
    for vid in state["violations"]:
        vid_escaped = re.escape(vid)
        if not re.search(
            rf"\[GATE:ACCEPTING:\s*{vid_escaped}\s*\]|"
            rf"\[GATE:CLEARED:\s*{vid_escaped}\s*\]",
            msg_lower,
            re.IGNORECASE,
        ):
            return False
    return True


def _log_acceptances(message: str, state: dict) -> None:
    """Log [GATE:ACCEPTING:<id>] or [GATE:CLEARED:<id>] to audit trail.

    Called when clearance tokens cause a bypass — records which violations
    the agent acknowledged, then removes them from the open violations state.
    Without this log, the clearance token is no better than the denylist it
    replaced (the agent can emit the token without any audit trail).
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
        if state["violations"]
        else ""
    )


# ── registration ──────────────────────────────────────────────────────

def register(ctx) -> None:
    """Register all lifecycle and tool-call hooks."""
    ctx.register_hook("pre_tool_call", on_pre_tool_call)
    ctx.register_hook("on_session_start", on_session_start)
    ctx.register_hook("subagent_stop", on_subagent_stop)
    ctx.register_hook("post_tool_call", on_post_tool_call)
    ctx.register_hook("pre_llm_call", on_pre_llm_call)
    ctx.register_hook("transform_tool_result", on_transform_tool_result)
    ctx.register_hook("on_output", on_output)
    ctx.register_hook("on_session_end", on_session_end)
