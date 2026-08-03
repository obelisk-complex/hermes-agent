# Self-Check Enforcer Plugin (v3.7.1)

The `self-check-enforcer` plugin mechanically enforces the 5-gate protocol. As of v3.7.1 it is a **bundled plugin** in the fork repo (`plugins/self-check-enforcer/`), on by default — it is no longer hand-installed under `~/.hermes/plugins/`. See `self-check-enforcement-system-v15.md` in the repo root for the complete spec, design notes, bypass table, and changelog.

## Architecture (8 hooks)

| Hook | Fires | Purpose |
|------|-------|---------|
| `on_session_start` | New session | Auto-load harness |
| `on_session_end` | Session ends | Clean up session-scoped state |
| `pre_tool_call` | Before any tool | Block `send_message` claiming ALL CLEAR while a violation is open; honour `[GATE:ACCEPTING:<id>]` clearance token |
| `post_tool_call` | After non-delegate tools | Detect FAIL in tool results via tight `_FAIL_PATTERN_SHORT` (excludes conjugations + adjacent punctuation); read-only tools exempt |
| `subagent_stop` | After each `delegate_task` child | Open a gate on FAIL / a failure status / `verdict: NEEDS_WORK` / `verdict: BLOCKED`; parse `VERIFIES_TASK: <id>` echo for auto-clear; build an enumerated per-id failed-check checklist |
| `pre_llm_call` | Once per turn | Re-inject the open-violation reminder + citation warnings |
| `transform_tool_result` | After a delegate result, before the model sees it | Annotate FAIL results with a `[GATE CHECK]` marker (SHORT pattern) |
| `on_output` | Final text, before the loop breaks | Block a completion claim while a gate is open; honour `[GATE:ACCEPTING:<id>]`; verify citations; flag claimed-but-missing files; 5 blocks → BLOCKED escalation |

## Key features (current)

- **Verdict READY / NEEDS_WORK / BLOCKED (v3.7.0–v3.7.1):** `subagent_stop` opens an escalation gate when a completed child reports `verdict: BLOCKED` / a non-null `escalation_reason` (no FAIL token needed), and a re-runnable gate on `verdict: NEEDS_WORK` (v3.7.1 — a contract-following child can no longer bypass by reporting NEEDS_WORK in prose without a literal FAIL). The delegate prompt mandates the verdict field, so enforcement does not depend on the child loading the skill.
- **Detection-first at `subagent_stop` (retained, v3.7.1):** the child-summary scan uses the full `\bFAIL\b`, so a bare FAIL gates even alongside a `READY` claim. A false positive is cheaply clearable with `[GATE:ACCEPTING:<id>]`; a false negative would be a silent bypass.
- **Split FAIL patterns:** `subagent_stop` uses full `\bFAIL\b`; `post_tool_call` / `transform_tool_result` use `_FAIL_PATTERN_SHORT`, which excludes conjugations (FAILED, FAILING, FAILURE, FAILOVER, FAILS, "FAIL to") and adjacent punctuation (`"`, `'`, `)`, `]`, `}`) to avoid false positives from source-code scanning tools.
- **Done-claim bypasses closed (v3.7.1):** `_claims_all_clear` also catches "verification complete", "task complete", "all findings addressed", and "nothing failed".
- **5-block → BLOCKED escalation (v3.7.0; extracted v3.7.1):** after 5 `on_output` blocks in a turn the gate stops retrying and escalates an explicit BLOCKED-to-a-human message; the decision lives in `agent/_on_output_gate.py` for direct unit testing.
- **`_blocked` / `_final_validated` (v3.6.0):** the in-loop block flag and the post-loop validation flag reset every loop iteration; `_final_validated` (set only on the allow path) replaced the old sticky `_on_output_fired` flag, so a budget-exhaustion exit re-checks a leaking claim.
- **Clearance tokens:** `[GATE:ACCEPTING:<id>]` per open violation clears the gate without all-clear phrasing (v3.6.0); `[GATE:CLEARED:<id>]` is also accepted. Audited via `_log_acceptances()`.
- **echo-based verifies_task:** the child echoes `VERIFIES_TASK: <id>`; the plugin parses it from `child_summary` (batch-safe, per-child — no cross-hook session-key assumption).
- **Enumerated per-id feedback (v3.7.0):** a violation detail is a per-id checklist of failed checks for a targeted retry.
- **Strict plan↔artifact matching (v3.7.0, advisory):** claimed file creations/writes whose target is absent on disk are flagged via `on_output`.
- **Citation checker:** single-pass file-exists + line-in-range in `on_output`.
- **Read-only tool exemption + audit trail:** read-only/content tools are exempt from FAIL scanning; `state["_audit_log"]` records every VERIFIED_CLEAR / ACCEPTED / CLEARED action with a timestamp.

## Violation flow

1. `delegate_task` returns FAIL (or a failure status, or `verdict: NEEDS_WORK|BLOCKED`) → `subagent_stop` opens a violation keyed by the child session id, with an enumerated checklist.
2. `subagent_stop` parses `VERIFIES_TASK: <id>` from the child summary → auto-clears the matching violation (batch-safe, per-child echo).
3. `pre_llm_call` re-injects the reminder (showing the id key) + citation warnings every turn while a violation is open.
4. `pre_tool_call` blocks a `send_message` completion claim (unless a clearance token is present).
5. `on_output` blocks a direct completion claim (unless a clearance token is present); runs the citation + claimed-file checks; logs acceptances.
6. After 5 `on_output` blocks → explicit BLOCKED escalation to a human.
7. A violation clears when: (a) a `verifies_task` echo clears it, (b) a `[GATE:ACCEPTING:<id>]` / `[GATE:CLEARED:<id>]` token logs and removes it, or (c) the session ends.

## Version history

| Version | Key change |
|---------|------------|
| v3.7.1 | Round-5 QA remediation: NEEDS_WORK enforced; done-claim bypasses closed; dead `_VERIFIES_TASK_RE` removed; `on_output` decision extracted to `agent/_on_output_gate.py`; detection-first retained at `subagent_stop`; **plugin now bundled in the repo**; daily sync hardened (pinned `checkout`, 4-suite pre-push gate) |
| v3.7.0 | Verification-protocol features: READY/NEEDS_WORK/BLOCKED verdict + BLOCKED escalation gate; strict plan↔artifact matching; enumerated per-id failed-check feedback; acceptance-scenarios in the delegate template |
| v3.6.0 | Round-1 QA remediation: `_blocked` reset per iteration + `_final_validated` (replacing the sticky `_on_output_fired`); FAIL no longer masked by "fixed"; clearance tokens clear without all-clear phrasing; hardened sync workflow |
| v3.5.x | Split FAIL patterns + punctuation exclusion; goal context in violations; GitHub Actions daily sync; Layer 4 (patch persistence) removed |
| v3.0–v3.3 | `on_output` hook; session-scoped state; `subagent_stop` detection; verifies_task; clearance tokens; citation checker |
