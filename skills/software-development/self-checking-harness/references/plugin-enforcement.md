# Plugin Enforcement

## Installation

The self-check-enforcer plugin lives at `~/.hermes/plugins/self-check-enforcer/`. It requires:

- `plugin.yaml` — manifest with hook declarations
- `__init__.py` — source with `register(ctx)` function

## Enabling/Disabling

```bash
hermes plugins enable self-check-enforcer
hermes plugins disable self-check-enforcer
```

## Plugin Load Order

User plugins (`~/.hermes/plugins/<name>/`) override bundled plugins on name collision. The enforcer is a user plugin.

## Hook Contract

All hooks receive `**kwargs` and should forward unknown kwargs with `**_`.
- `pre_tool_call`: return `{"action": "block", "message": "..."}` to block
- `post_tool_call`: return ignored (observational)
- `pre_llm_call`: return `{"context": "..."}` to inject
- `transform_tool_result`: return `str` to replace result
- `on_output`: return `{"action": "block", "message": "..."}` to block
- `on_session_start`: return ignored
