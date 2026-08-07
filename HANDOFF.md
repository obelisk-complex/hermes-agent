# HANDOFF — dual-signal approval gate (2026-08-07)

Resume point for the next session. The feature is COMPLETE, committed, and
pushed; the open items below are optional follow-ups, not blockers.

## Where things stand

- **Repo:** `/media/owner/Workspace/hermes-agent` (fork of NousResearch/hermes-agent,
  remote `origin` = obelisk-complex/hermes-agent, `upstream` = NousResearch).
- **Branch:** `feat/dual-signal-approval-gate-fresh` — pushed, parent = current
  `origin/main` (107c389e3), so the PR diff is exactly the feature.
- **Commits:**
  - `b0804f36b` — feat(approval): dual-signal auto-approval gate (16 files, +2096/−42)
  - `03502474a` — docs: memory.md — mark dual-signal gate as committed+pushed
- **Tests:** 654/654 green via `scripts/run_tests.sh` (22 files: 4 new + 18-file
  regression gate), re-verified after the rebase onto the 231-commit-newer main.
- **Worktree:** clean except untracked `.claude/` (Claude Code run artifacts —
  NOT part of the feature; do not commit).

## What shipped (commit b0804f36b)

Dual-signal auto-approval gate, ported from Cloudflare OS's Gatekeeper:
- `tools/action_tags.py` — frozen 23-tag taxonomy, ActionNature, CONFIGURABLE (12) /
  NEVER_AUTO_APPROVABLE / NOT_WIRED, pattern-key map for all 76 distinct
  DANGEROUS_PATTERNS descriptions (CI-enforced completeness test).
- `tools/auto_approval.py` — pure `evaluate_dual_signal()` truth table; `legacy`
  mode = today's verdict-only behaviour, bit-identical.
- `tools/approval.py` — `_resolve_tags` + D14 Hermes-home override (variant-based),
  fail-closed config reads (D16/G13), head-of-line barrier
  (`session_has_open_human_decision`: gateway queue + staleness-bounded `_pending`
  + CLI prompt depth; single lock acquisition — the `_lock` is NON-REENTRANT,
  never nest), `_manual_gate_scope` with subagent synthetic-callback marker,
  `clear_pending` outside lock scopes, T2a `hermes config set|unset|edit`
  detection entry (closes the self-rewrite hole).
- Both smart-approval sites wired (check_all_command_guards, execute_code);
  decision fields ride the SINGLE post_approval_response hook emission
  (plugin-hook tests assert exactly one — do not add a second observer).
- `hermes_cli/config.py` + `config_defaults.py` — `approvals.auto_approve`
  (default `legacy` = zero behaviour change until opted in), `auto_approve_tags`,
  `auto_approve_enabled_by` + post-write dependency warnings.
- `/approvals tags [enable|disable <tag>]` (T10) on gateway (admin-gated) + CLI,
  list round-trip via load_config+save_config (NOT set_config_value — it can't
  write lists), allowlist-bypass note.

## Docs updated (verified)

- `memory.md` (repo root) — project memory, committed in 03502474a.
- `/media/owner/Workspace/llm-wiki/wiki/sources/cloudflare-os-gatekeepers-2026-08.md`
  — "Implementation status (2026-08-07)" section appended.
- `/media/owner/Workspace/llm-wiki/wiki/log.md` — update entry appended.
- `/media/owner/Workspace/hermes-fixes-plans/2026-08-07-dual-signal-approval-gate-plan.md`
  — status note under the title. Full design + 5-round Opus audit trail lives
  there (`audit-round1-*.md` … `audit-round5-final.md` alongside).

## Open items (optional)

1. **Open the PR** — `https://github.com/obelisk-complex/hermes-agent/pull/new/feat/dual-signal-approval-gate-fresh`
   (or review the diff first: `git diff origin/main..origin/feat/dual-signal-approval-gate-fresh`).
2. **Phase B (documented in the plan, NOT implemented):**
   - T11 — tags on non-auto-approving surfaces (`_run_approval_gate`,
     `request_elicitation_consent` → `mcp.tool`, non-smart execute_code paths,
     `tools/write_approval.py:evaluate_gate`). No auto-approval path added.
   - T12 — MCP `readOnlyHint` → observation only (advisory by construction).
3. **memory.md rides in the PR** — if the PR should be code-only, move it out
   (amend/remove from branch) before opening.
4. **Repo venv note:** `.venv` had no pip/pytest; `ensurepip` + `pip install
   pytest pytest-asyncio pytest-timeout` were added (2026-08-07). CI uses the Nix
   devShell; local runs now work.

## Fork mechanics (important for future pushes)

`origin/main` is force-pushed DAILY by the fork's sync action (rebases onto
upstream). Any local branch based on an older `origin/main` diverges. Correct
pattern (see self-checking-harness `references/fork-push-workflow.md`): build
fresh from `origin/main` and cherry-pick your commits — do NOT rebase old
branches. This is why the branch is named `-fresh`.

## Environment / harness notes

- Delegation default: ollama nemotron-3-super:cloud (used for the two doc
  subagents; both READY, both verified by the orchestrator).
- The wiki-docs subagent's "missing acp module" test-suite note was a FALSE
  ALARM — `import acp` works and the feature diff touches no acp files.
- Terminal approval gate can block long/heredoc commands when the user is away —
  fall back to `execute_code` for verification.
