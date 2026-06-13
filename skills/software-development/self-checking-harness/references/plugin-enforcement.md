# Plugin Enforcement

## Installation

As of v3.7.1 the self-check-enforcer is a **bundled plugin** shipped in the repo at `plugins/self-check-enforcer/` — it loads automatically and is enabled by default, with no hand-installation. (Before v3.7.1 it was a user plugin under `~/.hermes/plugins/`.) It consists of:

- `plugin.yaml` — manifest with hook declarations
- `__init__.py` — source with a `register(ctx)` function

## Enabling/Disabling

```bash
hermes plugins enable self-check-enforcer
hermes plugins disable self-check-enforcer
```

## Plugin Load Order

User plugins (`~/.hermes/plugins/<name>/`) override bundled plugins (`<repo>/plugins/<name>/`) on name collision. The enforcer is now a **bundled** plugin (it was a user plugin before v3.7.1); `hermes plugins list` shows its `source` as `bundled`.

## Hook Contract

All hooks receive `**kwargs` and should forward unknown kwargs with `**_`.
- `pre_tool_call`: return `{"action": "block", "message": "..."}` to block
- `post_tool_call`: return ignored (observational)
- `pre_llm_call`: return `{"context": "..."}` to inject
- `transform_tool_result`: return `str` to replace result
- `on_output`: return `{"action": "block", "message": "..."}` to block
- `on_session_start`: return ignored
