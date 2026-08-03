---
name: self-checking-harness
description: "5-gate validation protocol for subagents plus information retrieval chain. Every delegate_task subagent must complete validation gates; every information fetch follows local-wiki → curl → web_extract → browser."
category: software-development
---

# Self-Checking Harness — 5-Gate Validation + Retrieval Protocol

## When to Use

Every subagent invoked via `delegate_task` MUST complete the 5 validation gates before
returning. Every information retrieval task MUST follow the retrieval chain.

**Pitfall: auto-load may silently fail.** Even with `always_load: [self-checking-harness]`
correctly set in `config.yaml`, the skill may not appear in the current session's system
prompt due to prompt caching. The config change only takes effect on the next session
(`hermes /new` or fresh invocation). If the harness directives (retrieval chain, gate protocol)
are not visible in your context, invoke the skill manually with `skill_view`. Do not assume
auto-load worked — verify by checking your system prompt for the harness content.

## Information Retrieval Protocol

When fetching a URL, file, or external resource, follow this chain — fastest/cheapest
first, only fall through on failure:

1. **Local wiki** — check `/media/owner/Workspace/llm-wiki/wiki/` first. The user maintains
   a personal knowledge base here with concepts, reports, comparisons, and sources.
   See `references/llm-wiki-workflow.md` for the full layout and search patterns.
2. **curl / wget** — for any plain-text URL (`.md`, `.txt`, `.json`, `.yaml`, `.csv`,
   `.xml`, `raw.githubusercontent.com`, documented API endpoints), fetch with `curl -sL`
   or `wget -qO-` in a terminal. No API key needed, fastest path.
3. **web_extract (Firecrawl)** — fall back only when curl fails (non-200, redirect loop,
   JS-rendered page, CAPTCHA wall). Requires `web.extract_backend: firecrawl` in config
   (DDG is search-only and cannot extract — see `references/web-extract-backend-pitfall.md`).
4. **browser** — last resort, for pages requiring login, interactive state, or complex JS.

**Never** start with `web_extract` or `browser` for a URL that curl can fetch.

## Core Protocol (5 Gates)

| Gate | What it requires |
|------|-----------------|
| **Gate 1 — Evidence** | Show specific files read/written, test output, command results, source URLs. |
| **Gate 2 — Confidence Score** | Assign 0.0–1.0. ≥ 0.7 required to pass. |
| **Gate 3 — Contradiction Check** | List any evidence that contradicts or qualifies the conclusion. |
| **Gate 4 — Alternative Explanation** | What else could explain the evidence? Why was it rejected? |
| **Gate 5 — Confidence Threshold** | If score < 0.7, specify what evidence would raise it. |

## Return Format

Every subagent MUST return:
```json
{
  "verdict": "READY | NEEDS_WORK | BLOCKED",
  "result": "...",
  "evidence": ["file:line or url or command:output"],
  "confidence": 0.0-1.0,
  "contradictions": "...",
  "alternatives_considered": "...",
  "escalation_reason": null or "..."
}
```

## Mandatory Subagent Context Block

**Every `delegate_task` call MUST include this block verbatim as the first content in the
`context` parameter.** No exceptions. This is mechanical — if you're calling delegate_task,
you prepend this block. The block encodes the 5-gate protocol, the retrieval chain, the
patch-only append rule, and the verify-before-acting rule directly into the subagent's
instructions so it cannot claim ignorance.

```
SELF-CHECKING HARNESS (mandatory — follow these rules or your output will be rejected):

1. RETRIEVAL CHAIN: local wiki → curl/wget → web_extract → browser. Never start with
   web_extract or browser for plain-text URLs.

2. 5-GATE VALIDATION — complete all gates before returning:
   Gate 1 — Evidence: show specific files read, test output, command results, source URLs.
   Gate 2 — Confidence Score: 0.0-1.0, must be ≥ 0.7 to pass.
   Gate 3 — Contradiction Check: list evidence that contradicts or qualifies your conclusion.
   Gate 4 — Alternative Explanation: what else could explain the evidence? why rejected?
   Gate 5 — Confidence Threshold: if score < 0.7, specify what evidence would raise it.

3. RETURN FORMAT — every response must end with:
   {"verdict":"READY|NEEDS_WORK|BLOCKED","result":"...","evidence":["..."],
    "confidence":0.0-1.0,"contradictions":"...","alternatives_considered":"...",
    "escalation_reason":null|"..."}

4. FILE WRITES: use patch tool to APPEND only. Never overwrite an existing file. If you
   need to add content to a report, use patch with the last 5 lines of the file as old_string
   and your new content as new_string.

5. VERIFY BEFORE ACTING: if you claim a gap exists, grep the target file to confirm it's
   genuinely absent. Subagent findings are self-reports, not verified facts.

6. ESCALATION LADDER: every delegate_task dispatch MUST be routed through the
   escalation-ladder skill. Before calling delegate_task, call
   escalate_delegate(goal, context) to auto-select the model tier. On failure, call
   handle_failure(goal, context, tier, error, baseline) to escalate. On completion,
   call restore_config(baseline). Never manually set delegation.model before dispatching.
```

## Key Rules

- **Information retrieval chain:** local wiki → curl/wget → web_extract (Firecrawl) → browser.
  Never start with web_extract for plain-text URLs.
- **Delegate-task decomposition:** Tasks >5 lines code, multi-step reasoning, config changes,
  network effects MUST use subagent with gates.
- **Re-verification after fixes:** The subagent that found bugs is NOT the one that verifies
  the fix — use a different agent.
- **No self-talk in output:** Never describe process/methodology/reasoning in the final output.
- **Source every factual claim:** Prices, stats, dates, entities must have linked URLs.
- **Verify by action, not vision:** A displayed/claimed state is a hypothesis — attempt the
  action and compare.
- **Subagent findings are self-reports, not verified facts.** A subagent that claims "X is
  missing from the plan" or "Y is not addressed" may be wrong — it may have read the plan
  superficially or hallucinated. Before acting on any subagent finding, grep the target
  file to confirm the claimed gap is genuinely absent. This is especially critical on re-audit
  rounds where subagents often report false positives against already-fixed content.
- **Mechanical harness prefix:** Every `delegate_task` call MUST prepend the Mandatory
  Subagent Context Block (above) as the first content in the `context` parameter. This is
  not optional — if you dispatch a subagent without this block, you have violated the harness.
  The block is self-contained; the subagent needs no other harness knowledge.
- **Mechanical escalation ladder:** Every `delegate_task` call MUST be routed through the
  `escalation-ladder` skill. Before calling `delegate_task`, call
  `escalate_delegate(goal, context)` from the escalation-ladder skill to auto-select the
  appropriate model tier. This is mechanical — no manual `hermes config set delegation.model`
  before dispatching. The skill handles classification (heuristic → router model), tier
  selection (cheap → capable → frontier), config toggling (ollama ↔ openrouter), and
  failure escalation (max 4 attempts). On task failure, call `handle_failure()` to escalate
  to the next tier. On completion, call `restore_config(baseline)` to restore the user's
  original delegation config. See the `escalation-ladder` skill for full API.

## Reference Files

- `references/llm-wiki-workflow.md` — local wiki layout, search patterns, and ingest workflow
- `references/web-extract-backend-pitfall.md` — DDG search-only limitation and fix
- `references/setup.md` — auto-load at session start, config pitfalls, verification
- `references/fork-push-workflow.md` — correct git pattern when origin/main has been force-pushed by upstream sync
