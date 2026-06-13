# Self-Check Enforcement System — Complete Specification

## What This Is

A multi-layer enforcement system that **mechanically prevents** the Hermes agent from reporting tasks as complete when subagent validation gates have actually failed. Built through 3 major versions (v1→v3), now at **v3.7.1**.

**v3.7.1 (round-5 QA remediation)** closed the gaps a second multi-agent audit found: the **NEEDS_WORK** verdict now opens a re-runnable gate on its own (a contract-following child reporting NEEDS_WORK in prose no longer bypasses the gate by omitting a literal FAIL token); `_claims_all_clear` closes done-claim bypasses ("verification complete", "task complete", "all findings addressed", "nothing failed"); the dead `_VERIFIES_TASK_RE` was removed; the `on_output` block / 5-block-escalation decision was extracted to an importable `agent/_on_output_gate.py` so it is unit-tested directly; the daily-sync workflow pinned `actions/checkout` to a SHA and now runs all four pre-push guard suites; and the delegate prompt now mandates the verdict field. Detection-first is retained at `on_subagent_stop` (a bare `FAIL` gates even alongside a `READY` claim). **The full suite is green.**

**v3.7.0 (verification-protocol features, bottega-inspired)** added: a **READY / NEEDS_WORK / BLOCKED** verdict in the return format, with `on_subagent_stop` opening a first-class **escalation gate** when a completed child reports `verdict: BLOCKED` / a non-null `escalation_reason` even with no FAIL token (and the 5-block exhaustion is now a clear BLOCKED escalation, not a soft warning); **strict plan↔artifact matching** (claimed file creations/writes whose target is absent are flagged — "done without the artifact" is a FAIL); an **acceptance-scenarios** field in the delegate template that the child must RUN and report (command + exit code) before claiming done; and **enumerated per-id failed-check feedback** for targeted retries.

**v3.6.0 (round-1 QA remediation)** fixed a set of correctness and robustness bugs found by a multi-agent audit:
- **Gate-clearing (was: stale gates pile up):** clearance tokens (`[GATE:ACCEPTING:<id>]`) now clear a gate *without* requiring all-clear phrasing (the documented honest-acknowledgement path used to be a silent no-op); tool-keyed violations now surface their `[tool:<name>]` id so they are clearable; every violation reminder points at the bracketed id.
- **Token/turn burn (was: agents loop on "continue"):** the conversation-loop `_blocked` flag is now reset per iteration, so a compliant retry actually breaks instead of looping to budget exhaustion; the post-loop hook revalidates output on budget-exhaustion exits via `_final_validated` (no more leaked all-clear claim).
- **FAIL-detection bypass:** the word "fixed" on a `FAIL` line no longer masks the failure; `_FAIL_PATTERN_SHORT` was rebuilt (dead conjugation lookahead and the `[\s]` typo removed).
- **Upstream fragility:** the daily sync force-push is now gated on a pre-push validation step (compile + guard tests); the post-pull syntax guard covers the three customised files; regression tests guard the on_output hook, the `_blocked` reset, and the `subprocess`-shadow class.

> ⚠️ **Authoritative source.** As of v3.7.1 the enforcer is a bundled plugin in the repo (`plugins/self-check-enforcer/`); `regen-v15-embedded.py` is retired. The source of truth is the tracked files `plugins/self-check-enforcer/__init__.py` and `plugins/self-check-enforcer/plugin.yaml` — verify behaviour against those and the test suites. Line numbers in design notes are informational; locate code by symbol.

## Architecture (3 Layers)

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
                    └─────────────────────────────┘
```

Every subagent gets all 3 layers. The harness auto-loads on session start. Gate violations are detected on `delegate_task` return, reinforced every turn via `pre_llm_call`, blocked on tool-call escape via `pre_tool_call`, and caught on direct-text-output escape via `on_output`.

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

**File:** `plugins/self-check-enforcer/plugin.yaml` — see the tracked file (no longer embedded here; it is now a bundled plugin in the repo).

### Plugin Source Code

**File:** `plugins/self-check-enforcer/__init__.py` — see the tracked file (bundled in the repo; this spec no longer hand-mirrors it).

### Detection Flow (Complete Cycle)

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

## Layer 4 — Source Modifications (on_output Hook + NO-OP Guard) — REMOVED v3.4

> **Note:** The source modifications previously documented in this section (on_output hook registration in `VALID_HOOKS`, hook call sites in `conversation_loop.py`, post-update apply hook in `main.py`, and NO-OP rejection guard in `delegate_tool.py`) are now committed directly in the Hermes repo source as native code changes. They survive `hermes update` without any external patch-persistence system. Refer to the repo source for current implementation details.

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

## Setup Guide

### Prerequisites

- Hermes Agent installed (any method)
- Python 3.10+

### Step-by-Step

```bash
# 1. Fork this repo on GitHub
#    Go to https://github.com/obelisk-complex/hermes-agent and click Fork
#    (or use: gh repo fork obelisk-complex/hermes-agent --clone)

# 2. Set remotes
git remote add upstream https://github.com/NousResearch/hermes-agent.git
git pull origin main                    # gets all custom commits

# 3. Create SOUL.md
cat > ~/.hermes/SOUL.md << 'EOF'
## Self-checking harness
**Pre-flight:** load self-checking-harness skill before each task. Info complete? rollback path? tools+access OK? known-good state before change? can outcome be proven?
**Post-action:** actual state matches config? previously-working still works? new errors? docs updated? temps cleaned?
EOF

# 4. Create plugin directory + files
mkdir -p ~/.hermes/plugins/self-check-enforcer
# Write plugin.yaml and __init__.py (from Layer 3 section in this doc)

# 5. Auto-sync (server-side)
#    The fork's .github/workflows/sync-upstream.yml runs daily at 0400 Pacific,
#    fetching upstream/main, rebasing custom commits, and pushing to origin.
#    No local machine needed — runs on GitHub's infrastructure.
#    Local `hermes update` only pulls from origin — no accidental force-pushes.

# 6. Verify
# Restart Hermes — the fork's source already has all on_output hooks
# and NO-OP guard committed. The plugin auto-loads harness on session start.
# delegate_task returning FAIL sets gate violation.
# send_message("ALL CLEAR") blocked when violation open.
```

### Verification Checklist

After install, confirm:

- [ ] SOUL.md exists and contains self-checking-harness instruction
- [ ] Plugin directory exists with `plugin.yaml` and `__init__.py`
- [ ] Plugin registers all 8 hooks: `pre_tool_call`, `post_tool_call`, `pre_llm_call`, `transform_tool_result`, `subagent_stop`, `on_session_start`, `on_session_end`, `on_output`
- [ ] `"on_output" in VALID_HOOKS` returns True
- [ ] `agent/conversation_loop.py` contains `on_output` hook call sites (2 locations)
- [ ] `tools/delegate_tool.py` contains `NO-OP REJECTION` guard
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

## Design Notes

1. **Why source modification instead of stdout wrapper:**
   A stdout wrapper lacks a feedback loop — the model proceeds unaware output was blocked. The core hook with `continue` injection forces retry. The process-level pipe is now optional defense-in-depth.

2. **Why git apply instead of monkey-patching:**
   `git apply` handles fuzz automatically, is machine-readable, survives `git stash`/`checkout` cycles, and can be tested with `--check` / `--reverse --check`.

3. **Why 5 retries (not 3):**
   Opus audit found 3 too tight for real scenarios where the model needs a couple of attempts to correct.

4. **Why changes are committed, not patched (v3.4 → v3.5.1):** On the `obelisk-complex/hermes-agent` fork, all source modifications are committed history. The GitHub Actions `Sync Upstream` workflow (daily at 0400 Pacific) merges upstream changes into the fork server-side. Local `hermes update` only pulls from `origin` — no fetching upstream, no rebasing, no force-pushing from local machines. No patches, no apply scripts, no post-merge hooks needed.

5. **Editable install (not reinstall needed):**
   `pip install -e .` means patches to tracked files take effect on next Hermes restart — no reinstall necessary.

6. **Plugin process-local state is correct:**
   Each subagent gets a fresh Python process with its own `_HARNESS_LOADED` flag. `on_session_start` fires for every new session, setting the flag. On agent restart it resets — each fresh session gets a fresh auto-load.

7. **File marker check replaced by committed code (removed v3.4):** Layer 3 originally imported `hermes_cli.plugins.VALID_HOOKS` on every session start. In v3.3 this was replaced with a `os.path.isfile()` marker check for performance. In v3.4 the session-start patch trigger is removed entirely — all changes are committed on the fork.

8. **NO-OP guard lives in tool handler, not conversation loop:**
   The instruction is injected into `_build_child_system_prompt()` in `tools/delegate_tool.py`, not in the conversation loop. This is because the subagent's system prompt is assembled entirely inside the delegate tool handler — the conversation loop only sees the final summary. Patching the tool handler is the only place where the instruction can be mechanically inserted below the agent's control.

9. **v3 session-scoped state (P0a):** Changed from module-level globals to `dict[session_id, state]` pattern with `_SESSION_LOCK`. Eliminates cross-session contamination in gateway mode where one process serves many sessions.

10. **v3 goal-scoped violation clearing (P0b):** Violations stored keyed by the delegate_task's goal text (first 200 chars). A clean result only clears violations matching its own goal — dispatching an unrelated clean task cannot clear the flag. The parent must re-use the same goal text or explicitly document each FAIL.

11. **FAIL_PATTERN simplified to `\bFAIL\b` only (v3→v3.4):** The original pattern included `\bconfidence<0.5`, `\bescalation_reason\b`, and `\bgate.*fail|violation` to catch edge cases, but every real violation path (NO-OP guard, child status fallback, plugin block messages) already produces `\bFAIL\b`. The extra alternatives caused false positives on descriptive analysis text ("confidence was 0.35 for audio segment 3") with zero additional recall. Reduced to pure `\bFAIL\b` — the only pattern that discriminative detection needs.

12. **v3 false positive fix (P1b):** Added `(?!\s+remain\b)` negative lookahead to the "no issues" pattern. "No issues remain after documenting each FAIL" (compliant output) now passes; "no issues found" (ALL CLEAR) still blocks.

13. **v3 compiled patterns:** `_FAIL_PATTERN` and `_FAIL_PATTERN_SHORT` compiled at module load, not re-compiled per call. Removed the 3 separate `re.compile()` calls in handlers. Later simplified from 4-alternative regex to pure `\bFAIL\b` — the extra alternatives provided no additional detection surface (all real violations produce FAIL) while causing false positives on descriptive text mentioning confidence thresholds.

14. **v3 on_session_end hook:** Registered to clean up session state on session end. Prevents memory leak from accumulated `_session_states` in long-lived gateway processes.

15. **v3.1 task_id-scoped violation keying:** Changed from `goal[:200]` text key to `task_id` (opaque UUID from hook payload). Eliminates false stickiness from rephrased goals and false clearing from shared goal boilerplate. The agent cannot forge a matching task_id — only explicit documentation clears the flag.

16. **v3.1 kwarg name fix:** Changed `kwargs.get("function_args")` to `kwargs.get("args", {})`. The `post_tool_call` handler previously relied on a fallback that happened to work (`kwargs.get("args")`). The `invoke_hook` in `_emit_post_tool_call_hook` (root-level `model_tools.py`; locate by symbol, not line) passes `args=function_args`, not `function_args=...`.

17. **v3.1 _session_states LRU cap:** `_session_states` changed from `dict` to `collections.OrderedDict` with a 1000-entry LRU eviction policy.

18. **v3.2 subagent_stop migration:** Switched delegate_task detection from `post_tool_call` to `subagent_stop`. The `subagent_stop` hook fires from the `invoke_hook("subagent_stop", ...)` call in `tools/delegate_tool.py` (locate by symbol — the line number drifts on every upstream rebase), independent of `handle_function_call` — sidesteps the #12922 debate entirely. Kwargs include `parent_session_id`, `child_session_id`, `child_summary`, `child_status` as named arguments. Keying on `child_session_id` (opaque UUID per dispatch) means no auto-clear on subsequent clean dispatches — the flag persists until explicit documentation or session end. `post_tool_call` retained for non-delegate tools only.

19. **v3.3 verifies_task auto-clear (feedback point 1, round6 fix):** The NO-OP guard in delegate_tool.py injects a mandatory instruction for the subagent to echo `VERIFIES_TASK: <id>` if the task context contains `verifies_task=<id>`. `on_subagent_stop` parses the child summary for this echo and auto-clears the matching violation when `child_status="completed"`. The echo is mechanically injected below the parent's reach — the parent cannot suppress it. This approach works in batch mode (each subagent echoes its own marker independently, unlike the v3.3.0 `pre_tool_call` capture which used a single session slot and cleared the wrong child in batch dispatches).

20. **v3.3 allowlist token inversion (feedback point 2, round6 audit fix):** Before v3.3, the system used a pure denylist of success idioms (`_claims_all_clear`) which is unbounded — every new "looks good" phrasing is another evasion vector. v3.3 inverts this: while violations are open, completion language is blocked unless the output carries `[GATE:ACCEPTING:<child_session_id>]` for every open violation. The `_has_clearance_token()` helper checks for these tokens. The denylist stays as belt-and-suspenders but is no longer load-bearing. The agent can only obtain clearance tokens by (a) re-running with verifies_task (mechanical clear) or (b) explicitly acknowledging each failure (honest override). **Round 6 addition:** `_log_acceptances()` is called when clearance tokens pass — it records each acknowledged violation to `state["_audit_log"]` with timestamp and action (ACCEPTED or CLEARED), then removes it from open violations. Without this log, the clearance token would be no better than the denylist (agent emits token, nothing records it).

21. **v3.3 citation checker (feedback point 3, round6 pragmatic fix):** The `on_output` handler runs `_verify_citations()` on the final response text, extracting `file.py:line` refs and verifying each against the filesystem via `_discover_hermes_root()` (imports `hermes_constants` at runtime, falls back to known paths). Issues are stored in `state["_citation_issues"]` and surfaced: (a) in `pre_llm_call` warnings next turn, and (b) appended to gate violation block messages when both apply. **Known limitations (round 6):** (i) Cannot catch wrong _function_ names like `_plugin_hooks.dispatch()` because the regex requires `file.py:line` format — missing either part and it doesn't match. (ii) Cannot catch wrong line numbers on real files (e.g. `delegate_tool.py:2306` when the real call is at 2344) because the check only guards `lineno > total_lines`, not whether the cited _symbol_ is at that line — a symbol-proximity grep would be needed for that, which is a materially different check. (iii) Runs in `on_output` only (single pass), not duplicated in `post_llm_call`. Honest boundary: the checker catches nonexistent files and lines-past-EOF but cannot verify semantic accuracy of citations. This is explicitly stated as a limitation rather than shipping security theatre.

22. **CI tests are the new Layer 4 persistence check (v3.5):** The old Layer 4 used 4 patch files + an apply script to verify source changes survived `git pull upstream main`. Now that all changes are committed on the fork, the GitHub Actions `Tests` workflow serves the same role at higher fidelity — it runs the full pytest suite (140+ tests, 6 parallel slices) on every push and daily sync. Instead of "did the patch apply?" (binary file check), it answers "do all modifications still work?" (functional validation). If a rebase breaks our custom NO-OP guard, on_output hook, or auto-rebase logic, the failing test catches it immediately — same detection surface as the old patch system, but orders of magnitude more thorough. The `Sync Upstream` workflow + `Tests` workflow together form a complete replacement for the old patch persistence: sync merges changes, tests verify they still work.

---

23. **v3.4 FAIL regex false-positive filter (round 8 gate fix):** `_FAIL_PATTERN` changed from `\bFAIL\b` to `\bFAIL\b(?!.*(?i:\bfixed\b))`. When a subagent summary contains "FAIL #1 — FIXED" throughout section headers, the gate scanner previously saw FAIL and flagged it as a violation — even though every FAIL was followed by FIXED describing a remediated issue. The negative lookahead suppresses matches when FIXED appears after FAIL on the same line, case-insensitively. FAIL itself remains case-sensitive.

24. **v3.4 on_output retry loop scope fix (round 7 QA):** The in-loop on_output's `continue` and `break` targeted the inner `for _ores` loop, not the outer `while` loop. Fixed by introducing a `_blocked` flag: for loop sets `_blocked = True` and breaks; afterwards, if blocked and retry limit not exceeded, `continue` targets the while loop triggering a real LLM retry. `_blocked` initialized at function scope so the post-loop guard can reference it.

25. **v3.4 post-loop double-fire guard (round 7 QA):** Post-loop hook guarded with `and not _blocked` so normal text completions that already fired the in-loop hook skip the second invocation. Budget-exhaustion and error exits bypass the in-loop handler, so `_blocked` stays False and the post-loop hook fires correctly for non-standard exits.

26. **v3.4 Source patches idempotent (round 8):** All 4 patch files in `~/.hermes/patches/` updated to match the committed-on-fork source code. `003-on-output-update-hook.patch` no longer contains `import subprocess, os` (removed the redundant local import that caused `UnboundLocalError`). `002-on-output-conversation-loop.patch` updated to reflect the `_blocked`-flag fixed version. The `apply-on-output-patches.sh` script's existing idempotency logic now reports `✓ Already applied` for all 4 patches instead of `⚠ Cannot apply (conflict)`.

27. **v3.4 FAIL_PATTERN_SHORT kept in sync:** Updated identically to FAIL_PATTERN for consistency.

28. **v3.4 on_post_tool_call read-only tool exemption (round 9):** `on_post_tool_call` exempts `read_file`, `search_files`, and `web_extract` from FAIL scanning. These tools return content verbatim, so "FAIL" in their output is always descriptive text — not a tool-operation failure.

29. **v3.4 Layer 4 removed — patch persistence system gutted (round 10):** All 6 patch files, `apply-on-output-patches.sh`, the post-update hook in `main.py`, the session-start trigger in `__init__.py`, and the daily cron job removed. Changes are now committed on the fork and auto-rebased.

30. **v3.5 GitHub Actions daily sync (round 11):** `.github/workflows/sync-upstream.yml` runs daily at 04:00 Pacific (11:00 UTC). Fetches upstream/main, rebases custom commits, force-pushes to origin. Silent when up-to-date. Also triggerable from the Actions tab. Runs on GitHub infrastructure — no local machine needed.

31. **v3.5 CI test fixes (round 11):** Two upstream tests fixed:
    - `test_empty_context_ignored` / `test_goal_only`: Our VERIFIES_TASK instruction contained literal `CONTEXT`. Changed to `"goal or description"`.
    - `test_update_on_fork_checks_upstream_when_origin_up_to_date`: Mock needed `return_value=None` because the function now returns bool (MagicMock default is truthy).

32. **v3.5.1 Local auto-rebase removed (round 12):** The `_sync_with_upstream_if_needed` call in `hermes update` is removed. `hermes update` now only pulls from `origin` — no fetching upstream, no rebasing, no force-pushing from local machines. The `Sync Upstream` GitHub Actions workflow (daily at 0400 Pacific) is the sole mechanism for merging upstream changes. Anyone who clones this fork gets the code without their `git push` touching an unintended repo. The GH Actions `GITHUB_TOKEN` is ephemeral and single-repo-scoped — safe by design. Two tests updated to reflect the removed local sync.

33. **v3.5.2 on_output double-fire fix (round 13):** The post-loop guard in `conversation_loop.py` was changed from `not _blocked` to `not agent._on_output_fired`. Previously, the `not _blocked` guard only suppressed the post-loop invocation on the blocked path — for normal allowed completions, `_blocked` stayed False and the hook fired twice per turn. A separate `_on_output_fired` flag is now set unconditionally after the in-loop hook runs (line 4180), regardless of whether the hook returned a block or allow. The post-loop guard checks this flag instead, firing only when the in-loop hook never ran (budget exhaustion exits). The 5-block exhaustion warning is preserved — the flag is set during the final iteration, so the post-loop guard correctly skips rather than clobbering the warning.

34. **v3.5.3 Goal context + tighter FAIL filter (round 14):** Three fixes for agent confusion:
    - **Key in violation detail**: subagent failure detent now prefixes each violation with `[{child_session_id}]` or `[tool:{name}]`, so the agent can read which ID to use for `[GATE:ACCEPTING:<id>]`. The `pre_llm_call` reminder already referenced "the child_session_id is shown in the violation detail below" — now it actually is.
    - **Tighter `_FAIL_PATTERN_SHORT`**: excludes English conjugations (`FAILED`, `FAILING`, `FAILURE`, `FAILOVER`, `FAILS`, `FAIL TO`) via negative lookahead, reducing false positives in natural-language tool output and agent responses. `subagent_stop` still uses the full `_FAIL_PATTERN` (`\bFAIL\b` with fixed negative lookahead) since subagents output structured `FAIL:` markers.
    - **Post_tool_call switched to SHORT pattern**: uses `_FAIL_PATTERN_SHORT` instead of `_FAIL_PATTERN`, reducing false positives from tools that return text containing conjugated "fail" words.
    - **`skill_view` exemption**: added to the read-only tool exemption list alongside `read_file`, `search_files`, `web_extract`, `patch`. The tool returns skill document content, which may contain "FAIL" as descriptive text.

---

## Git Diff Summary (All Changes)

```
 hermes_cli/plugins.py             |  5 +++++
 agent/conversation_loop.py        | 63 +++++++++++++++++++++++++++++---
 hermes_cli/main.py                | 91 ++++++++++++++++++++++++++++++++
 tools/delegate_tool.py            | 16 +++++++++
 .github/workflows/sync-upstream.yml | 47 ++++++++++++++++++++++
 ~/.hermes/plugins/self-check-enforcer/__init__.py | 2 regex lines changed
                                                     (v3.4)
 ~/.hermes/plugins/self-check-enforcer/plugin.yaml | +2 hooks declared
 7 files changed, ~232 insertions
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
| `on_session_start()` | New session created | Ignored | Auto-load harness; set `_HARNESS_LOADED = True` |
| `on_session_end()` | End of `run_conversation` | Ignored | Clean up session-scoped state from `_session_states` dict |
| `on_output(response_text, ...)` | Final text produced, before loop break | `{"action": "block", "message": "..."}` or `None` | Block ALL CLEAR text output while violation open; accept `[GATE:ACCEPTING:]` clearance token bypass (v3.3); verify file:line citations in final text (v3.3) |
| `pre_gateway_dispatch(message)` | Gateway received user message | `{"action": "skip"/"rewrite"/"allow"}` | (Not used) |
| `subagent_stop(parent_session_id, child_session_id, child_summary, child_status)` | After each delegate_task child finishes | Ignored | Detect FAIL in child_summary via `_FAIL_PATTERN`; auto-clear matching violation on `verifies_task` re-run (v3.3) |
