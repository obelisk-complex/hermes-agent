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
- [ ] `~/.hermes/SOUL.md` contains the self-checking-harness block
- [ ] new session: a subagent auto-loads the harness (no manual `skill_view`)
- [ ] `delegate_task` returning `FAIL` (or `verdict: NEEDS_WORK` / `BLOCKED`)
      opens a gate
- [ ] `send_message("ALL CLEAR")` is blocked while a gate is open
- [ ] direct success text is blocked by `on_output` while a gate is open
- [ ] repeated blocks end in an explicit BLOCKED escalation, not a silent "done"
- [ ] full suite passes: `cd <hermes_root> && python3 -m pytest`
