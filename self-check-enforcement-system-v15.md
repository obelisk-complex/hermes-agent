# Self-Check Enforcement System: Install Guide

How to add this fork's self-check enforcement system to a **vanilla Hermes
install** (an upstream `NousResearch/hermes-agent` checkout), including the
`on_output` source hook it rides on.

The system has four pieces:

1. **`on_output` source hook**: a hook point in the agent core that fires on the
   model's final text response (no tool calls) and lets a plugin block it and
   force a retry. Not in upstream yet, so it must be added to the source.
2. **`self-check-enforcer` plugin**: registers the hooks that mechanically gate
   completion: detect `FAIL` / `NEEDS_WORK` / `BLOCKED` from subagents, block
   "all clear" claims while a gate is open, and escalate to a human after
   repeated blocks.
3. **`self-checking-harness` skill**: the validation protocol subagents follow.
4. **SOUL.md block**: an advisory line that tells the agent to load the harness.

Path conventions: `<hermes_root>` = your Hermes repo checkout
(e.g. `~/.hermes/hermes-agent`); `~/.hermes/` = your Hermes home (config) dir.

## Prerequisites

- A working Hermes install (any method); Python 3.10+.
- `<hermes_root>` is a git checkout installed editable (`pip install -e .`), so
  source edits take effect on the next restart, so no reinstall is needed.

## 1. Add the `on_output` hook to the source

The generic hook lives on the fork's `proposal/on-output-hook` branch
(`hermes_cli/plugins.py`, `agent/conversation_loop.py`,
`agent/_on_output_gate.py`, + tests, and nothing else). Apply it onto your checkout:

```bash
cd <hermes_root>
git remote add selfcheck https://github.com/obelisk-complex/hermes-agent.git
git fetch selfcheck proposal/on-output-hook
git cherry-pick selfcheck/proposal/on-output-hook   # 3-way apply; leaves one commit
```

Not a git checkout? Apply the diff directly instead:

```bash
curl -L "https://github.com/NousResearch/hermes-agent/compare/main...obelisk-complex:hermes-agent:proposal/on-output-hook.diff" \
  | git -C <hermes_root> apply --3way
```

Confirm:

```bash
python3 -c "from hermes_cli.plugins import VALID_HOOKS; print('on_output' in VALID_HOOKS)"   # -> True
test -f <hermes_root>/agent/_on_output_gate.py && echo "gate module present"
```

## 2. Install the enforcer plugin, harness skill, and SOUL docs

These are additive files. Pull just those paths from the fork without switching
your install over to it:

```bash
cd <hermes_root>
git fetch selfcheck main
git checkout selfcheck/main -- \
  plugins/self-check-enforcer \
  skills/software-development/self-checking-harness \
  docs/self-check
```

(Or copy those three paths from a clone of the fork.) The plugin is on by
default once present.

## 3. Add the SOUL.md block

Append to `~/.hermes/SOUL.md` (injected into every session, regardless of cwd):

```markdown
## Self-checking harness
**Pre-flight:** load self-checking-harness skill before each task. Info complete? rollback path? tools+access OK? known-good state before change? can outcome be proven?
**Post-action:** actual state matches config? previously-working still works? new errors? docs updated? temps cleaned?
**Walk the history first (Chesterton's Fence):** before changing code, find out WHY it is the way it is (`git log -- <file>`, `git log -S '<symbol>'`, `git blame`, then read the introducing commit's message). Code that looks dead/unused/redundant is a hypothesis, not a conclusion - it may have been deliberately retired or superseded; confirm from history before "fixing", removing, or re-enabling it.
**Name behaviour changes:** enabling/disabling a control, changing a default, tightening/loosening validation, or removing a branch is a behaviour change, not a "cleanup" - keep/add a regression test for the PRIOR behaviour and verify the new one. Config (env vars, flags, DB rows, secrets) is the runtime source of truth, not repo defaults; state the value you are assuming and confirm it before relying on it or calling a change safe.
**Fail loud, not silent:** a security- or behaviour-relevant action that is silently suppressed (swallowed exception, silent `return False`, dropped record, skipped post) is a latent incident. Make it observable (log at warning+ or emit a metric) and flag it in review.
**Push/merge pre-flight:** before an outward, hard-to-reverse action (push, PR create, PR merge) confirm the user gave an explicit go-ahead for THIS action (approval for one does not extend to the next), history was walked, behaviour changes were flagged with a regression test, and verification actually ran and passed.
**Verify by action, not vision:** a displayed/claimed "locked / blocked / disabled / done" state is a hypothesis, not a conclusion - attempt the action and compare what is claimed against what actually happens (exit 0 is not success). A surface that claims one state but behaves as another is itself a bug to report.
```

## 4. (Optional) delegate-tool enforcement glue

For the strongest enforcement (a mandatory verdict field and a no-op-rejection
instruction injected into every subagent prompt *below the agent's reach*), also
take the fork's `tools/delegate_tool.py` changes (cherry-pick or diff them from
`selfcheck/main`, same as step 1). The plugin still gates completion without
them (via `on_output` + `subagent_stop`); this only closes the
"dispatch a no-op subagent to clear the gate" path.

## 5. Restart and verify

Restart Hermes, then confirm:

- [ ] `"on_output" in VALID_HOOKS` is `True`; `agent/_on_output_gate.py` exists
- [ ] `agent/conversation_loop.py` has the `on_output` call sites and the
      `_final_validated` post-loop guard
- [ ] `plugins/self-check-enforcer/` present (`plugin.yaml` + `__init__.py`); the
      plugin registers all its hooks: `pre_tool_call`, `post_tool_call`,
      `pre_llm_call`, `transform_tool_result`, `subagent_stop`,
      `on_session_start`, `on_session_end`, `on_output`
- [ ] `~/.hermes/SOUL.md` contains the self-checking-harness block, including
      the walk-the-history, name-behaviour-changes, fail-loud, push/merge
      pre-flight, and verify-by-action lines (see also the parity appendix)
- [ ] new session: a subagent auto-loads the harness (no manual `skill_view`)
- [ ] `delegate_task` returning `FAIL` (or `verdict: NEEDS_WORK` / `BLOCKED`)
      opens a gate
- [ ] `send_message("ALL CLEAR")` is blocked while a gate is open
- [ ] direct success text is blocked by `on_output` while a gate is open
- [ ] repeated blocks end in an explicit BLOCKED escalation, not a silent "done"
- [ ] full suite passes: `cd <hermes_root> && python3 -m pytest`

## Appendix: Claude-harness parity guards (v3.7.3)

The reference Claude-Code harness this system is modelled on enforces two more
guards *mechanically*, as `PreToolUse` hooks, alongside the completion gate.
**v3.7.3 ports both into the `self-check-enforcer` plugin.** Hermes cannot
inject-and-allow on `pre_tool_call` (it is block-only: its return is consumed by
`get_pre_tool_call_block_message`, which honours only `{"action": "block",
"message": ...}` and turns the message into the tool result), so each guard maps
to the hook whose timing fits it:

| Claude-Code hook | Behaviour | Hermes port (v3.7.3) |
|---|---|---|
| `PreToolUse(Edit\|Write)` → Chesterton's Fence | On the first file edit per (session, repo) in a git repo, surface the recent `git log` plus a reminder to understand WHY the code is shaped as it is before changing/removing/re-enabling it. Never blocks. | `transform_tool_result` on `write_file` / `patch` / `skill_manage`: on the first edit per (session, repo) it **appends** the reminder + `git log -15` to the result. Never blocks. Fires once per repo via `_get_state(session_id)`. |
| `PreToolUse(Bash)` → push/merge pre-flight | Before `git push` / `gh pr create` / `gh pr merge`, present a checklist: explicit per-action go-ahead, history walked, behaviour changes flagged with a regression test, verification actually run, feature branch not default. | `pre_tool_call` on `terminal` matching `git push` / `gh pr (create\|merge)`: **blocks once** with the checklist (surfaced as the tool result), then self-clears for that exact command for the session, so re-running the same command proceeds. |

Design notes:

- **Why the asymmetry.** `transform_tool_result` fires *after* the tool runs, so
  it can only append - perfect for an advisory nudge, useless for a pre-flight
  (the push would already be done). `pre_tool_call` fires *before* the tool runs
  but can only block. So a real *pre*-flight has to block. The Chesterton nudge
  therefore lands before your *next* action in the repo, not before the first
  edit - the closest a never-blocks hook can get to the reference timing.
- **The push guard is the only one here that blocks** - a deliberate, approved
  exception to the otherwise never-block rule, because stopping before an
  outward, hard-to-reverse action is the whole point. It is keyed on the exact
  command and self-clears, so it nudges once and cannot wedge the turn (re-run to
  proceed). The block message is worded as a CHECKPOINT, not a failure, so the
  model re-runs rather than treating the push as having failed.
- Both extend handlers the plugin already registers (`transform_tool_result`,
  `pre_tool_call`); no new hook registrations, so the hook count stays 8.
- Tests: `tests/plugins/test_self_check_guards.py` (history-append, fire-once,
  non-git silence, error-result skip, delegate regression; push block-once,
  self-clear, `gh pr create` match, non-push pass-through).
