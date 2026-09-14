# Self-Check Enforcer: a reference design for the `pre_verify` plugin hook

This plugin is a worked example of Hermes's **`pre_verify`** hook: a gate that fires
when the agent is about to stop, with no further tool calls. It mechanically stops the
agent from reporting a task complete while a delegated subagent's validation gate is
still open.

It is **mandatory on this fork** and cannot be disabled through the normal plugin
config/CLI path. A fresh clone of this repo loads it as a bundled plugin
(`plugins/self-check-enforcer/`) with no hand-installation, and the loader ignores
`plugins.enabled`/`plugins.disabled` for this plugin ID — see
`FORK_MANDATORY_PLUGIN_KEYS` in `hermes_cli/plugins.py`. `hermes plugins disable
self-check-enforcer` refuses with an explanation rather than silently no-opping.

## Why `pre_verify` (vs `pre_tool_call` / `pre_llm_call`)

The other hooks gate *tool calls* and *LLM turns*. `pre_verify` is the only hook that
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
   `pre_verify` blocks a completion claim while any gate is open; `on_pre_tool_call`
   blocks an "ALL CLEAR" `send_message`; `on_pre_llm_call` re-injects the open
   violation each turn. The honest clear path is `[GATE:ACCEPTING:<id>]`.

The hook *plumbing* is upstream's: `pre_verify` is dispatched from
`agent/turn_stop_gates.py`. The fork widens that gate's precondition so it also
fires on turns with no file edits, and adds `pre_verify_terminal_substitute` so
an exhausted retry budget escalates instead of shipping the unverified answer.
The escalation text itself lives in this plugin.

## What ships and where

| Layer | Artifact |
|---|---|
| 3 | `plugins/self-check-enforcer/{__init__.py,plugin.yaml}` |
| 2 | `skills/software-development/self-checking-harness/SKILL.md` |
| 1 | `docs/self-check/SOUL.example.md`, `docs/self-check/SOUL.block.md` |

## Deeper reference

Install/update guide (adding this system to a vanilla Hermes install): [`self-check-enforcement-system-v15.md`](../../self-check-enforcement-system-v15.md).
