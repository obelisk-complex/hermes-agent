# hermes-agent — project memory

Durable facts about this codebase and its conventions. Not a task log — keep
entries that will still matter in future sessions. (2026-08-07: created after
the dual-signal approval gate implementation.)

## Dual-signal auto-approval gate (committed b0804f36b, pushed to obelisk-complex/hermes-agent branch feat/dual-signal-approval-gate-fresh, based on origin/main, 2026-08-07)

Ported from Cloudflare OS's Gatekeeper. Design/audit trail:
`/media/owner/Workspace/hermes-fixes-plans/2026-08-07-dual-signal-approval-gate-plan.md`
(rev. 5, READY after 5 Opus audit rounds) + `audit-round{1-5}-*.md` beside it.

Architecture (all verified against source at 9d87ffc80):
- `tools/action_tags.py` — frozen ActionTag enum (23 tags, values are config
  surface, never renamed), ActionNature dataclass, CONFIGURABLE_TAGS (12) /
  NEVER_AUTO_APPROVABLE / NOT_WIRED, `tag_for_pattern_key` keyed on the
  canonical DANGEROUS_PATTERNS description strings. Imports nothing from
  Hermes (D2) — safe to import at module scope from approval.py.
- `tools/auto_approval.py` — pure `evaluate_dual_signal()` truth table; rule
  order load-bearing: legacy (verdict alone) < off < untagged <
  never_auto_approvable < head_of_line < all(tags enabled).
- `tools/approval.py` — `_resolve_tags` (+ D14 override via
  `_HERMES_HOME_TARGET`, run on `_command_detection_variants` not raw text),
  `_get_auto_approve_mode` (last-known-good on read failure, junk→off D16),
  `_get_auto_approve_tags` (non-list rejected wholesale, D11),
  `session_has_open_human_decision` (gateway queue + `_pending` w/
  `_pending_at` staleness + `_manual_prompt_depth`, single lock acquisition,
  D13 — NEVER nest the non-reentrant `_lock`), `_manual_gate_scope` (skips
  callbacks marked `_hermes_synthetic_approval`), `clear_pending` (must be
  called OUTSIDE `with _lock:`; resolve_gateway_approval calls it after the
  lock scope).
- Wired at both smart-approval sites: check_all_command_guards Phase 2.5
  (~:4179) and check_execute_code_guard (~:4579). T2a added the
  `hermes config set|unset|edit` DANGEROUS_PATTERNS entry (flag-tolerant,
  read verbs excluded) — tagged config.write, never auto-approvable.
- Config keys: `approvals.auto_approve` (legacy default), `auto_approve_tags`
  (list), `auto_approve_enabled_by`. `warn_auto_approve_dependencies` in
  `hermes_cli/config.py` fires post-write beside the cron warning.
- T10: `/approvals tags [enable|disable <tag>]` in
  `hermes_cli/approval_mode.py` (shared runner), dispatched in
  `gateway/slash_commands.py` (admin-gated) and
  `hermes_cli/cli_commands_mixin.py`. Writes via load_config+save_config —
  NOT set_config_value (can't write lists).

## Hard-won invariants / traps

- **Plugin-hook single-emission contract** (test_approval_plugin_hooks.py):
  each smart verdict fires EXACTLY one `post_approval_response` with
  `choice=smart_approve/smart_deny`, `decided_by=aux_llm`. Dual-signal
  decision fields (action_tags, auto_approval_reason, enabled_by,
  dual_signal_outcome) ride THAT emission via the `decision` kwarg of
  `_observe_smart_approval_verdict` — a second emission breaks the frozen
  sequence assertion. Audit R4-7's "no hook fires on the edited branch"
  premise was wrong.
- **Headless early-return**: check_all_command_guards returns
  `{"approved": True}` at the ~:4010 `not is_cli and not is_gateway and not
  is_ask` gate BEFORE detection runs. Command-gate tests must fake
  `_is_interactive_cli=True` (+ stub prompt_dangerous_approval). execute_code
  tests use `HERMES_EXEC_ASK=1` instead.
- **`_ApprovalEntry(data)`** takes one positional arg (data dict), not
  (session_key, data).
- Non-reentrant `_lock`: any helper that takes `_lock` itself (clear_pending,
  has_blocking_approval, submit_pending) cannot be called inside `with
  _lock:` — deadlocks permanently. Watchdog subprocess tests catch this.
- Pyright reports `Import "tools.*"/"hermes_cli.*"/"utils" could not be
  resolved` repo-wide (no pythonpath config) — pre-existing false positive,
  not a real error. lint (ruff) status is the gate.

## Conventions

- Test runner: `scripts/run_tests.sh` ONLY (per-file subprocess isolation,
  TZ=UTC, PYTHONHASHSEED=0). Bare pytest misses isolation guarantees.
  Venv: repo `.venv` lacked pip+pytest (2026-08-07: `ensurepip` +
  `pip install pytest pytest-asyncio pytest-timeout` added them).
- AGENTS.md rubric: prompt-cache prefix is sacred; core is a narrow waist
  (new model tools are last resort); don't write change-detector tests;
  verify the premise against the code before "fixing" (intentional design
  often looks like a gap).
- Plan discipline: implementation plans for core changes are drafted by Opus
  via Claude Code CLI and audit-refactor-looped (plan-auditor +
  conformance-auditor + blind-spot-auditor, fresh Opus agents per round, one
  report file each to avoid write collisions, write-early instruction,
  `--output-format json` captured for turn/cost parsing) until READY. Every
  auditor finding must be source-verified before fixing — auditors missed a
  real second auto-approval site at :4146 once; conformance+blind-spot both
  caught it.
- `hermes config set` cannot write list values (type coercion only
  bool/int/float/str) — list config must go through code or config.yaml.
