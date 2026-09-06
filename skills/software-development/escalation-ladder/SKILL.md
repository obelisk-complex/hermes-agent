---
name: escalation-ladder
description: Use when dispatching subagents via delegate_task. Automatically routes tasks through a capability-tiered cascade — local classifier → cheap cloud → capable cloud → frontier — based on task difficulty. Wraps delegate_task so every subagent dispatch picks the right model tier without manual model selection.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [delegation, routing, cost-optimization, model-selection, cascade]
    related_skills: [self-checking-harness, plan-audit-loop]
---

# Escalation Ladder

Automatically routes `delegate_task` calls through a capability-tiered cascade:
local classification → cheap cloud → capable cloud → frontier.

## When to Use

- Any time you call `delegate_task` and want automatic model-tier selection
- When you want cost-optimized routing (cheap model for simple tasks, frontier for hard ones)
- When you want failed tasks to automatically retry at higher capability tiers

Don't use for:
- Tasks where you explicitly need a specific model (use `force_tier` or direct `delegate_task`)
- Single tool calls that don't need delegation at all

## Architecture

```
Task goal + context
        │
        ▼
┌──────────────────┐
│  Heuristic       │  ← zero-cost keyword/pattern matching
│  Pre-filter      │    catches ≥50% of tasks
└────────┬─────────┘
         │ (if inconclusive)
         ▼
┌──────────────────┐
│  Router Model    │  ← local gemma4:9b (free, <2s)
│  Classifier      │    classifies into 4 difficulty levels
└────────┬─────────┘
         │
    ┌────┼────┬──────────┐
    ▼    ▼    ▼          ▼
 Tier 1  Tier 2/3    Tier 4
 Cheap   Capable     Frontier
 flash   pro:cloud   opus-4.8
         (same model
          for 2 & 3)

On failure: escalate to next tier (max 4 attempts).
After Tier 4 failure: return error to orchestrator.
```

## Classification Tiers

| Level | Label    | Criteria | Model |
|-------|----------|----------|-------|
| 1     | TRIVIAL  | Single-file edit, simple query, <50 chars goal | `deepseek-v4-flash:cloud` |
| 2     | MODERATE | Multi-file change, moderate complexity | `deepseek-v4-pro:cloud` |
| 3     | COMPLEX  | Architecture change, new feature, debugging | `deepseek-v4-pro:cloud` |
| 4     | FRONTIER | Security audit, plan audit, cross-system integration | `anthropic/claude-opus-4.8` |

MODERATE and COMPLEX currently share the same model — intentional, preserves headroom
for future tier expansion. Effective routing is 3-tier with a 4-level classifier.

## Usage

```python
from scripts.escalate_delegate import escalate_delegate

# Automatic tier selection
result = escalate_delegate(
    goal="Add docstring to calculate_total()",
    context="File: src/utils.py, function calculate_total at line 42"
)

# Force a specific tier (skip classification)
result = escalate_delegate(
    goal="Audit this deployment plan",
    context="...",
    force_tier=4
)
```

## Configuration

Tier models are configured in `scripts/escalate_delegate.py` — edit the `TIER_CONFIG`
dict to change models per tier. Provider and base_url are read from the active
Hermes delegation config at runtime.

## Prerequisites

- Ollama installed and running on `localhost:11434`
- Router models pulled: `ollama pull gemma4:9b` and `ollama pull qwen3.6:27b`
- Dispatch models accessible: `deepseek-v4-flash:cloud`, `deepseek-v4-pro:cloud`
- `OLLAMA_API_KEY` and `OPENROUTER_API_KEY` in `~/.hermes/.env`
- Hermes delegation config set in `~/.hermes/config.yaml`

## Common Pitfalls

1. **Not re-entrant.** `escalate_delegate` mutates global `delegation.model` and  
   `delegation.base_url`. Two concurrent calls will race. Serialize your dispatches.

2. **Router model must be local.** The classifier runs on a local Ollama model to  
   keep it free. If Ollama isn't running, the skill falls back to heuristic-only  
   classification.

3. **Provider toggling.** When switching between ollama and openrouter, the skill  
   sets `delegation.base_url` accordingly. If the skill crashes mid-dispatch, the  
   config may be left in the wrong state. Check `hermes config` after a crash.

4. **Max 4 attempts.** A task that fails at every tier will stop after Tier 4 and  
   return the error. No infinite retry loops.

5. **Inconsistent provider configuration for tiers 1-3.** The `TIER_CONFIG` for  
   tiers 1-3 sets provider to "ollama" but specifies cloud models (e.g.,  
   `deepseek-v4-flash:cloud`). This will cause delegation to fail because those  
   models aren't available via Ollama. Change provider to "openrouter" for these tiers.

6. **Potential command injection in `_call_ollama`.** The function uses user-controlled  
   input (goal and context) to construct the prompt passed to the `ollama run` command.  
   Although the current implementation does not use a shell, there is a theoretical risk  
   if the `ollama` command itself is vulnerable to argument injection. Ensure the `ollama`  
   command is not susceptible to argument injection (e.g., by not allowing prompts that  
   start with `-`). Consider validating or sanitizing the prompt if high security is required.

7. **Incomplete method classification in docstring.** The docstring for the `classify`  
   function lists a possible return method of 'fallback', but this method is never returned  
   by the function. The actual methods returned are 'force', 'cache', 'heuristic', and 'router'.  
   Update the docstring to remove the 'fallback' method from the list of possible return values.

8. **Non-atomic configuration updates.** The `set_tier` and `restore_config` functions  
   update the delegation configuration in three separate steps (provider, base_url, model).  
   If one step fails, the configuration may be left in an inconsistent state. Implement  
   error handling that attempts to revert any successful steps if a subsequent step fails.  
   Alternatively, delegate the atomicity concern to the Hermes configuration system if it  
   supports batch updates.

9. **Unbounded failure cache growth.** The `FailureCache` only removes entries based on  
   age (24-hour TTL) but does not limit the total number of entries. Over time, the cache  
   file could grow without bound. Implement a maximum number of entries (e.g., 1000) and  
   remove the oldest entries when the limit is exceeded.

10. **Potential model availability in fallback.** The `FRONTIER_FALLBACK` model is set to  
    "openai/gpt-5.6-sol", which may not be a valid or available model. If the fallback is  
    triggered due to an infrastructure failure at tier 4, the delegation may fail if this  
    model is not accessible. Verify the availability of the fallback model or replace it  
    with a known, reliable model.

11. **Context truncation in router classification.** The context passed to the router model  
    is truncated to 500 characters. This may remove important information that could affect  
    the classification accuracy. Consider increasing the limit or implementing a smarter  
    truncation strategy (e.g., keeping the beginning and end of the context).

12. **Missing concurrency control (race condition).** No locking mechanism exists to prevent  
    race conditions when multiple coroutines/threads call `set_tier()` simultaneously.  
    Concurrent calls could leave configuration in inconsistent state, causing subsequent  
    tasks to use wrong models.

13. **Unhandled subprocess failure modes.** `_hermes_config_get()` and `_hermes_config_set()`  
    use `subprocess.run()` but don't handle all failure modes:  
    - No handling for when `hermes` command is not in PATH (FileNotFoundError)  
    - No handling for when subprocess is killed mid-execution  
    - No handling for partial output or encoding issues  
    This can lead to silent failures where configuration appears to change but actually  
    didn't, leading to wrong model usage.

14. **Resource leak in Ollama subprocesses.** `_call_ollama()` uses `subprocess.run()` but  
    doesn't ensure cleanup if process hangs beyond timeout. While TimeoutExpired is caught,  
    the subprocess may still be running as an orphan, leading to accumulation of zombie  
    ollama processes consuming memory over time.

15. **Single point of failure: Configuration file access.** Multiple functions read/write  
    the same configuration via `hermes config get/set` without checking if the command succeeds.  
    If the configuration system becomes unavailable (permissions, disk full, corruption),  
    all calls fail silently, causing configuration drift where the system believes it's using  
    one model but actually using another.

16. **Cache directory creation race condition.** `FailureCache._ensure_dir()` uses  
    `mkdir(parents=True, exist_ok=True)` but between the existence check and creation,  
    another process could create it as a file. Subsequent cache operations fail with  
    confusing errors when trying to read/write a file as directory.

17. **Atomic write vulnerability.** `FailureCache._write()` uses temp file + rename which  
    is good, but:  
    - No handling for when rename fails (e.g., cross-device link)  
    - No cleanup of temp file on failure  
    - No validation that written JSON is readable  
    This can lead to corrupted cache files or leftover temp files consuming disk space.

18. **Insufficient input validation.** While there's basic type checking for `goal` and  
    `context`, there's no validation of:  
    - Extremely long strings that could cause DoS  
    - Malicious content that could break subprocess calls  
    - Null bytes or other problematic characters  
    Potential for command injection or resource exhaustion via crafted inputs.

19. **Missing circuit breaker pattern.** No mechanism to temporarily stop calling a  
    consistently failing service (e.g., if Ollama is down). Every failure causes a new  
    subprocess attempt, potentially worsening the situation during outages.

20. **Silent degradation to fallback modes.** When router models fail, the system silently  
    defaults to Tier 2 without clear indication to caller. The warning goes to stderr but  
    the calling agent may not notice or act on it. Agents making decisions based on  
    incorrect tier assumptions without knowing it.

21. **Supply chain risk: Hardcoded external dependencies.** Relies on specific external  
    services: Ollama, OpenRouter, specific model names. No abstraction or configuration  
    for alternative providers. Difficult to adapt to new providers or when existing ones  
    change APIs.

## Verification Checklist

- [ ] `ollama list` shows gemma4:9b, qwen3.6:27b, deepseek-v4-flash:cloud, deepseek-v4-pro:cloud
- [ ] `hermes config` shows delegation.provider and delegation.model set
- [ ] Test: TRIVIAL task routes to flash
- [ ] Test: FRONTIER task routes to opus-4.8
- [ ] Test: force_tier=3 skips classification
- [ ] Test: failed task escalates to next tier
- [ ] Test: Tier 4 failure returns error (no infinite loop)
