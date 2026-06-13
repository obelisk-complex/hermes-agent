# Self-Check Enforcer Plugin (v3.5.4)

The `self-check-enforcer` plugin at `~/.hermes/plugins/self-check-enforcer/` mechanically enforces the 5-gate protocol. See `self-check-enforcement-system-v15.md` in the fork repo root for the complete spec, design notes, bypass table, changelog, and GitHub Actions daily sync workflow.

## Architecture (8 Hooks)

| Hook | Fires | Purpose |
|------|-------|---------|
| `on_session_start` | New session created | Auto-load harness |
| `on_session_end` | Session ends | Clean up session-scoped enforcement state |
| `pre_tool_call` | Before any tool executes | Block `send_message` claiming ALL CLEAR while violation open; check `[GATE:ACCEPTING:]` clearance token |
| `post_tool_call` | After non-delegate tools return | Detect FAIL in tool results using tight `_FAIL_PATTERN_SHORT` (excludes English conjugations + punctuation); exempts read_file, search_files, session_search, memory, web_extract, patch, skill_view |
| `subagent_stop` | After each delegate_task child | Detect FAIL in child summary via `_FAIL_PATTERN` (full); parse `VERIFIES_TASK: <id>` echo for auto-clear; violation detail now includes child session ID |
| `pre_llm_call` | Once per turn, before LLM | Inject gate-violation reminder (with child_session_id shown) + citation warnings |
| `transform_tool_result` | After tool returns, before model sees | Annotate FAIL results with `[GATE CHECK]` marker (uses SHORT pattern) |
| `on_output` | Final text produced, before loop break | Block ALL CLEAR text; accept `[GATE:ACCEPTING:]` bypass; verify citations; log acceptances to audit trail |

## Key Features (v3.5.4)

- **Split FAIL patterns**: subagent_stop uses full `\bFAIL\b` (subagents emit structured FAIL: markers); post_tool_call and transform_tool_result use tight `_FAIL_PATTERN_SHORT` excluding English conjugations (FAILED, FAILING, FAILURE, FAILOVER, FAILS, FAIL TO) AND adjacent punctuation (`"`, `'`, `)`, `]`, `}`) to prevent false positives from source-code scanning tools (grep, ripgrep, cat).
- **Goal context in violations**: Each violation detail prefixes the child_session_id or tool:name key, so the agent can read which ID to use for `[GATE:ACCEPTING:<id>]`.
- **Double-fire fix**: Separate `_on_output_fired` flag (not `_blocked`) ensures the hook fires exactly once per turn for normal completions while still firing for budget-exhaustion exits.
- **echo-based verifies_task**: subagent instructed to echo `VERIFIES_TASK: <id>` if context contains `verifies_task=<id>`. Plugin parses echo from child_summary -- batch-safe, no cross-hook session-key assumption.
- **Allowlist token inversion**: `[GATE:ACCEPTING:<id>]` per open violation bypasses the denylist. Audited via `_log_acceptances()`.
- **Citation checker**: single-pass in `on_output` -- file-exists + lineno-in-range only. Does NOT catch wrong function names (no `:line`) or wrong line numbers on real files.
- **Read-only tool exemption**: read_file, search_files, session_search, memory, web_extract, patch, skill_view exempt from FAIL scanning (content tools return verbatim text).
- **Audit trail**: `state["_audit_log"]` records every VERIFIED_CLEAR / ACCEPTED / CLEARED action with timestamp.

## Violation Flow

1. `delegate_task` returns FAIL - `subagent_stop` sets violation via `_FAIL_PATTERN`; violation detail prefixed with `[child_session_id]`
2. `subagent_stop` parses `VERIFIES_TASK: <id>` from child summary - auto-clears matching violation (batch-safe, per-child echo)
3. `pre_llm_call` injects reminder (showing the child_session_id key) + citation warnings every turn while violation open
4. `pre_tool_call` blocks `send_message` ALL CLEAR (unless clearance token present)
5. `on_output` blocks direct text ALL CLEAR (unless clearance token present); runs citation check; calls `_log_acceptances()` when bypassed
6. After 5 blocks - abort message
7. Flag clears when: (a) verifies_task echo clears it, (b) clearance token logs and removes it, (c) session ends

## Version History

| Version | Key Change |
|---------|------------|
| v3.5.4 | _FAIL_PATTERN_SHORT excludes adjacent punctuation to prevent false positives from source-code scanning |
| v3.5.3 | Goal context in violations; tighter FAIL_PATTERN_SHORT; split patterns per site; skill_view exemption |
| v3.5.2 | on_output double-fire fix (_on_output_fired flag) |
| v3.5.1 | Local auto-rebase removed (GH Actions sole sync mechanism) |
| v3.5 | GitHub Actions daily sync workflow, CI test fixes |
| v3.4 | Layer 4 (patches) removed, FAIL regex filter, read-only exemption |
| v3.3 | subagent_stop detection, verifies_task, clearance tokens, citation checker |
| v3.2 | subagent_stop hook migration |
| v3.1 | task_id-keyed violations, LRU cap, kwarg fix |
| v3.0 | on_output hook, session-scoped state, regex fixes |
