# Self-Check Enforcer: a reference design for the `on_output` plugin hook

This plugin is a worked example of Hermes's **`on_output`** hook: a gate that fires on
the agent's *final text response* (when it stops, with no further tool calls). It
mechanically stops the agent from reporting a task complete while a delegated
subagent's validation gate is still open.

It ships **on by default**. A fresh clone of this repo loads it as a bundled plugin
(`plugins/self-check-enforcer/`) with no hand-installation. To disable it, add it to
`plugins.disabled` in `config.yaml`:

```yaml
plugins:
  disabled: [self-check-enforcer]
```

## Why `on_output` (vs `pre_tool_call` / `pre_llm_call`)

The other hooks gate *tool calls* and *LLM turns*. `on_output` is the only hook that
gates the **final answer**: the moment the agent declares it is done. That makes it
the right place to enforce "do not claim completion while a gate is open": the plugin
returns `{"action": "block", "message": ...}` on a completion claim, injecting a retry
turn instead of letting the loop end. After 5 blocks it escalates BLOCKED to a human.

## The three layers

1. **SOUL (advisory)**: tells the agent to load the harness skill and to verify
   before claiming. Adopt it via `docs/self-check/SOUL.block.md` (drop into any SOUL)
   or use the full example `docs/self-check/SOUL.example.md`.
2. **`self-checking-harness` skill (protocol)**: the 5-gate self-check + the
   `verdict: READY | NEEDS_WORK | BLOCKED` return format. Bundled at
   `skills/software-development/self-checking-harness/`.
3. **This plugin (mechanical)**: 8 hooks. `on_subagent_stop` opens a gate when a
   child returns FAIL / a failure status / `verdict: NEEDS_WORK|BLOCKED`;
   `on_output` blocks a completion claim while any gate is open; `on_pre_tool_call`
   blocks an "ALL CLEAR" `send_message`; `on_pre_llm_call` re-injects the open
   violation each turn. The honest clear path is `[GATE:ACCEPTING:<id>]`.

The hook *plumbing* lives in the agent core: the `on_output` call sites in
`agent/conversation_loop.py`, `"on_output"` in `hermes_cli/plugins.py` `VALID_HOOKS`,
and the 5-block decision in `agent/_on_output_gate.py`.

## What ships and where

| Layer | Artifact |
|---|---|
| 3 | `plugins/self-check-enforcer/{__init__.py,plugin.yaml}` |
| 2 | `skills/software-development/self-checking-harness/SKILL.md` |
| 1 | `docs/self-check/SOUL.example.md`, `docs/self-check/SOUL.block.md` |

## Deeper reference

Install/update guide (adding this system to a vanilla Hermes install): [`self-check-enforcement-system-v15.md`](../../self-check-enforcement-system-v15.md).
