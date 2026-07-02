# Model Bakeoff — spec & plan (v4, post-build run-blocker conformance)

Date: 2026-06-29. Repo: hermes-agent fork, branch `feat/model-bakeoff`.
Status: BUILT. A post-build conformance re-audit (#8) found the implementation sound on the
core but missing/weak on five run-blocking axes (A to E); those are now fixed under TDD and
re-audited. A follow-up hardening pass then closed four correctness findings ON the fixes
themselves (M1-M3, L1; see §15) under the same plan->audit->TDD discipline. 101 offline tests
green. Six should-fix + five low findings are DEFERRED and tracked in §15. The v3 plan history
is retained below for traceability.

**Run-blocker fixes folded into this spec (A to E):**
- **A — contamination detection** uses a per-task denominator (`n_attempted`, not a
  roster-wide `n_healthy`); see §4.
- **B — reasoning controls** are sent via a per-model `reasoning_extras` registry field
  (DeepSeek thinking models carry `{"thinking": {"type": "enabled"}}`); a zero-reasoning run
  by a reasoning model is flagged loud; see §3/§5.
- **C — bar exclusion** drops sub-bar models from the ladder and warns loudly when the
  ladder collapses to <= 1 entry; see §2/§4.
- **D — sandbox hardening:** the in-sandbox timeout is decoupled from `api_timeout_s`
  (default 60s), with a per-stream output cap and a best-effort child heap cap; see §6.
- **E — `--repeats` default is 1** (was 3), matching the N=1 ladder semantics; see §5.

## 1. Goal / end state
A reproducible dev-tool harness that runs a fixed roster of LLMs over a curated,
tiered coding corpus, measures **correctness + latency + cost**, and emits (a) a
ranked report **with confidence intervals**, and (b) a proposed
`quality_gate.model_ladder` (flat list, weakest -> strongest) plus a per-tier rung.
It does NOT auto-apply anything (quality-gate stays dormant; applying the ladder is a
separate human step). Output is **indicative, not authoritative** (see §13).

## 2. Objective + ranking FORMULA
**Cost/latency-aware quality**, as a deterministic lexicographic order WITHIN a tier:
1. keep models that **clear the bar** (pass_fraction >= tier threshold, §4);
2. **report** sort: pass_fraction desc, cost_per_task asc, p50 latency asc, name;
3. cost_per_task = (prompt_tok*price_in + completion_tok*price_out)/n for metered;
   **subscription models = $0 marginal** (§8) and are ranked after metered models
   that clear the bar (their marginal cost is genuinely zero to the user but
   unquantifiable as a comparator), annotated as such;
4. **reasoning and non-reasoning models are ranked in separate groups**, never merged.
- **Ladder assembly direction (PL1):** the emitted `model_ladder` is **weakest-first**
  = pass_fraction **ASC** (the REVERSE of the report's DESC sort). `claude-opus-4-8`
  is pinned **last** as the declared ceiling even if it does not top pass_fraction;
  the report annotates this. `load_ladder()` reads index 0 = weakest; `next_rung`
  escalates forward, so direction matters.

## 3. Roster + per-model config (registry.py)
Explicit flags so the client never guesses. Wire ids `(verify)` confirmed at preflight.

| Model | Gateway | Wire id | cost_model | reasoning | omit_temp | max_tok | api_timeout |
|---|---|---|---|---|---|---|---|
| DeepSeek V4 Flash | opencode-go | `deepseek-v4-flash` | subscription | **yes** | **yes** | 16k | 240s |
| DeepSeek V4 Pro | opencode-go | `deepseek-v4-pro` (verify) | subscription | yes | yes | 16k | 240s |
| GLM-5.1 | opencode-go | `glm-5.1` | subscription | no | no | 8k | 180s |
| GLM-5.2 | opencode-go | `glm-5.2` (verify) | subscription | no | no | 8k | 180s |
| Qwen 3.5:397b | ollama-cloud | (verify via /v1/models) | **subscription** (Ollama Pro) | yes | yes | 16k | 300s |
| Qwen3 Coder 480B | ollama-cloud | `qwen3-coder:480b` (preflight live-test) | **subscription** (Ollama Pro) | no | no | 16k | 300s |
| Qwen3 Coder Next | ollama-cloud | `qwen3-coder-next` (preflight live-test) | **subscription** (Ollama Pro) | no | no | 16k | 300s |
| Qwen 3.7 Max | opencode-zen | `qwen3.7-*` (verify) | **metered** | yes | yes | 16k | 240s |
| Kimi K2.6 | opencode-go (NOT zen) | `kimi-k2.6` (verify) | subscription | yes (server default) | yes | 16k | 240s |
| MiniMax M3 | opencode-zen | `minimax-m3` (preflight live-test) | **metered** | yes | yes | 16k | 240s |
| **Opus 4.8 (ceiling)** | opencode-zen | `claude-opus-4-8` | **metered** | no | no | 8k | 180s |

- **Temperature mechanism (PM1, corrected):** the registry `omit_temp` flag is the
  SOLE source of truth for whether `temperature` is sent. Do NOT derive it from
  `profile.fixed_temperature` (verified: `OpenCodeGoProfile.fixed_temperature is None`,
  NOT `OMIT_TEMPERATURE`; Hermes omits Kimi temp via a model-name check in
  `agent/auxiliary_client.py:_fixed_temperature_for_model`). When `omit_temp` is set
  the client sends no temperature; else `temperature=0`. Profiles are reused ONLY for
  thinking/reasoning controls via `profile.build_api_kwargs_extras(model=...)`.
- **DeepSeek V4 Flash (PM3):** the codebase `_is_deepseek_thinking_model` already
  classifies it a thinking model -> `reasoning: yes, omit_temp: yes`; preflight only
  confirms reachability, not thinking status.
- **Kimi K2.6 (PL2):** reasoning at server default (no explicit effort param;
  opencode-go relay manages depth). Reproducibility note: can pin via
  `reasoning_config={'enabled': True, 'effort': 'medium'}`; default is server-managed.
- **MiniMax M3 (PM4):** native MiniMax uses `anthropic_messages` on api.minimax.io;
  routing via opencode-zen chat_completions is UNVERIFIED. Preflight sends a minimal
  completion; if it errors or returns garbage, **M3 is excluded from the run with a
  loud note** (contingency: a direct MiniMax key could be added later). No silent drop.
- **Kimi routing (A-H7):** opencode-go (not zen); existing `_is_kimi_k2_model` +
  `OpenCodeGoProfile` carry the Moonshot thinking controls. Verified at preflight.
- **Reasoning controls mechanism (run-blocker B):** each `ModelSpec` carries an optional
  `reasoning_extras` dict that `client.build_payload` DEEP-copies into the request body (never
  aliasing the shared registry literal, so a future per-call field cannot corrupt it; M2). DeepSeek
  V4 Flash/Pro set `{"thinking": {"type": "enabled"}}`, MIRRORING `OpenCodeGoProfile`'s
  behaviour for deepseek thinking models. It is mirrored as an explicit registry literal,
  NOT imported, because that provider plugin lives in a hyphenated, non-importable dir
  (`plugins/model-providers/opencode-zen/`). Kimi K2.6 and Qwen 3.5 reason IN-BAND via
  `<think>` tags (no structured reasoning-token controls) so they carry no `reasoning_extras`;
  the zero-reasoning guard (§5) therefore treats a CLOSED `<think>...</think>` block OR
  `thinking_tokens > 0` as evidence of reasoning (a bare `<think>` substring in code/prose does
  NOT count; M1) to avoid both a false alarm and a silent miss on those two.

## 4. Corpus (tasks/)
~20 **original** coding tasks (not copied from public benchmarks), each = `prompt.md`
+ a HIDDEN oracle + a **reference solution**. Tiers: ~8 quick, ~8 standard, ~4
thorough. Bar: quick = all pass; standard/thorough >= 0.8.
- **Oracle validation (A-M):** every reference solution MUST pass its oracle offline
  before any run; a failing reference = a broken oracle. Required free pre-run gate.
- **Anti-gaming (PM6, was Hypothesis):** Hypothesis is NOT a dependency (not installed;
  would break collection). Instead, quick-tier oracles use **parameterised pytest with
  many diverse, seeded inputs** so a hardcoded-expected-value stub cannot pass; combined
  with oracle isolation (§6) this defeats both extraction and memorisation. (Property-
  based testing noted as an optional future enhancement, gated behind an opt-in extra.)
- **Contamination detection (B-M/PM; run-blocker A):** after a run, flag any task where
  **>= max(2, floor(0.75 * n_attempted))** models score perfect, where `n_attempted` is the
  count of models that produced a usable (non-call-error) run **for that specific task** — a
  PER-TASK denominator, not a roster-wide `n_healthy`, so a model that errored on this task
  does not dilute the threshold. Tasks with < 2 attempters are never flagged. Flagged tasks
  -> manual review / exclusion from ladder derivation; the report always carries a
  "Contamination flags" section ("none detected" when empty).

## 5. Components (one module each; offline unit tests, zero API calls)
- `env_loader` — **reuse** `hermes_cli/env_loader.py:load_hermes_dotenv()` to load
  `~/.hermes/.env` (verified to set `OPENCODE_GO_API_KEY` / `OPENCODE_ZEN_API_KEY` /
  `OLLAMA_API_KEY`; auth.json holds only `source: env:...` refs + fingerprints).
- `registry.py` — the §3 table; no secrets inline.
- `client.py` — async OpenAI-compatible caller. MUST: set `User-Agent: hermes-cli/<v>`
  on every request incl. `/v1/models` (WAF 403s default UA); apply the registry
  `omit_temp` + `max_tok`; enforce per-model `api_timeout_s` on the HTTP call;
  **cache-bust** every call with a UUID nonce in the prompt + cache-disable header where
  supported; record TTFT + total latency separately, prompt/completion/**thinking**
  tokens separately, a `cache_hit` flag (<100ms => warn + retry once). Reasoning
  controls (not temperature) reuse `profile.build_api_kwargs_extras`.
- `extractor.py` — code from prose+fences: ```python -> ``` any -> whole response;
  empty => `extraction_failed=True`, score 0, flagged distinct from "wrong answer".
- `sandbox.py` — runs extracted code vs oracle in an isolated subprocess (§6); the
  wall-clock timeout (default 60s, run-blocker D) is decoupled from the model's
  `api_timeout_s`; output is file-backed and per-stream capped (no in-memory balloon).
- `scorer.py` — parses pytest; distinguishes collection/import/syntax **error**
  (non-zero exit, no results) from genuine test failures -> `error_type`; a capped/truncated
  sandbox run maps to `ERR_OUTPUT_CAP` (checked after timeout, before returncode).
- `runner.py` — orchestrates roster x corpus (N default 1); threads each model's
  `reasoning_extras` into the call (run-blocker B); passes a fixed `sandbox_timeout`
  (default 60s, run-blocker D), NOT the API timeout; per-gateway ping baseline; **per-model
  warm-up immediately before that model's batch** (§10); budget enforcement; writes artifacts.
- `rank.py` — §2 formula -> report (Wilson 95% CIs) + `ladder.yaml` (weakest-first, §2/§9);
  `detect_contamination` (§4) and the bar-exclusion + degenerate-ladder notes (run-blocker C).
- `cli.py` — `validate-oracles`, `preflight`, `estimate`, `run`. `--repeats` defaults to **1**
  (run-blocker E; N=1 ladder semantics); `run` also exposes `--bar` (default 0.8,
  ladder-inclusion threshold) and `--sandbox-timeout` (default 60s). A zero-reasoning run by a
  reasoning model emits a loud per-model WARNING note. (A `report` re-render subcommand is
  deferred, §15.)

## 6. Security — untrusted model code (CRITICAL)
Separate subprocess; fresh temp dir; `env` scrubbed to a minimal allowlist (NO
.env/auth/token vars); process-group kill on hard timeout; output size-capped. **Oracle
isolation:** oracle test file OUTSIDE any model-reachable dir; pytest `--import-mode=
importlib`, rootdir outside the writable temp dir, `--ignore-glob` for model-authored
`test_*.py` so only the oracle is collected. Best-effort network denial; documented as
NOT a hard boundary (no root on WSL); tasks need no net/fs, so a solution touching either
is itself flagged.
- **Resource caps (run-blocker D):** child stdout/stderr go to FILES (not pipes), so a
  runaway writer can neither deadlock the parent nor balloon parent memory; each stream is
  read back at most `MAX_OUTPUT_BYTES` (1 MB) and flagged `truncated` (-> `ERR_OUTPUT_CAP`)
  when capped. A POSIX `preexec_fn` sets `RLIMIT_FSIZE` (1 MB per file) and best-effort
  `RLIMIT_AS` (1 GB child heap; verified to pass a normal pytest run, blocks a 2 GB alloc).
  The wall-clock timeout is a fixed `sandbox_timeout` (default 60s), decoupled from the
  per-model `api_timeout_s`.
- **Residual limits (best-effort, NOT closed):** `RLIMIT_FSIZE` bounds a SINGLE file, not
  total disk — a child can still write many sub-cap files. On kernels where `SIGXFSZ` is
  ignored/handled, an oversized write surfaces as `EFBIG` (the file is capped, the process
  continues) rather than a kill, so `truncated` may not latch on that path. `RLIMIT_AS` is
  POSIX-only and assumes the pure-python corpus (no heavy native imports needing > 1 GB).

## 7. Auth / secrets
Reuse `~/.hermes/.env` via `env_loader` (three keys verified present + active). No new
secrets; tokens never written to `runs/` or logs. Missing key => preflight fails loud.

## 8. Budget / cost safety
- **Metered = opencode-zen ONLY** (Qwen 3.7 Max, MiniMax M3, Opus 4.8). opencode-go and
  ollama-cloud are flat subscriptions the user already pays = **$0 marginal**; the cap
  governs only Zen spend.
- `estimate` (dry run): token-count prompts, assume a per-task completion budget,
  multiply by Zen prices. For **reasoning models apply a `thinking_budget_multiplier`
  (default 4.0)** to the completion budget (PM/BM) so projected $ and time are not a 2x
  underestimate; the multiplier is labelled and adjustable. Also report the expected
  **Wilson CI width** for the chosen N + corpus size before any spend.
- `run` enforces a HARD cap (default $10) on Zen cost; abort loud if exceeded. The stopped
  model's already-completed task runs are carried out on the `BudgetExceeded` exception
  (`partial_runs`) and persisted + counted, so a budget-stop never silently discards partials
  even with the repeats=1 default (M3). Per-model `max_tok` bounds worst-case completion.

## 9. Persistence (never generate-judge-discard)
To `runs/<run-id>/`: per (model,task) raw prompt (with nonce), raw response, extracted
code, pytest output, `error_type`, `extraction_failed`, TTFT, total latency,
prompt/completion/thinking tokens, cost, `cache_hit`; plus `summary.json` (per-gateway
ping baselines, per-model warm-up latencies), `report.md` (Wilson CIs; overlapping-CI
"not distinguishable" notes; reasoning vs non-reasoning groups; subscription-vs-metered
annotation; contamination flags), and `ladder.yaml` in the EXACT consumer schema,
weakest-first:
```
quality_gate:
  model_ladder:    # matches load_ladder() in plugins/quality-gate/ladder.py
    - <weakest>
    - ...
    - claude-opus-4-8   # ceiling, pinned last
```
Run-id + resulting ladder recorded in the brain.

## 10. Process
`validate-oracles` (offline) -> `preflight` (resolve wire ids, reachability, UA, auth;
live-test MiniMax M3 on Zen) -> `estimate` -> `run`. The runner warms up **each model
immediately before that model's task batch** (PM2/BM3: no idle gap, so a late-scheduled
model can't go cold); the re-warm trigger compares a model's **two warm-up inference
calls** (re-warm if the 2nd > 2x the 1st) — NOT warm-up vs the HTTP ping baseline
(PM2/BM4: dimensionally mismatched). Then `report` -> record. TDD: extractor/scorer/rank/
sandbox have offline unit tests (synthetic fixtures, zero API) BEFORE any live run.
**Commit + push `feat/model-bakeoff` to the fork after EACH session** (`hermes update`
resets local main hard; unpushed branch work is at risk).

## 11. Rollback
Isolated dir on its own branch; nothing live touched; quality-gate dormant; the ladder
is a recommendation file. Rollback = abandon/delete branch + `runs/`.

## 12. Success criteria
- `validate-oracles` green (every reference solution passes its oracle) BEFORE run;
- offline unit tests for extractor/scorer/rank/sandbox pass (no network);
- preflight resolves the roster, loudly flags unreachable models / wrong wire ids, and
  live-tests MiniMax M3 on Zen (excluding it loudly if unserved);
- cache-miss verified (no sub-100ms `cache_hit` calls in the timed run);
- a full `run` completes within the Zen metered cap and persists all artifacts;
- report has pass-fraction CIs, reasoning/non-reasoning split, subscription annotation,
  contamination flags; emits a schema-correct, weakest-first `ladder.yaml`;
- results sanity-checked vs benchlm.ai (Opus 4.8 top; GLM-5.2 ~ DeepSeek V4 Pro);
  divergences explained.

## 13. Risks / limitations (stated in the report)
- **Statistical:** N=1 over ~20 tasks => ~+/-22pt CIs; mid-ladder positions may be noise.
  Reported with CIs + indistinguishable flags + opt-in N>1; ladder is indicative.
- **Cross-gateway latency:** includes network/region overhead; reported with a ping
  baseline + caveat; not pure inference latency.
- **Reasoning commensurability:** thinking models presented in a separate group.
- **Contamination:** "original" != unseen; fraction-based detection flag + manual review.
- **ToS:** Anthropic ToS restricts competitive benchmarking; this is internal
  quality-gate calibration (not training/publishing a competing product), results stay
  internal; if in doubt, treat the Opus 4.8 ceiling as a fixed benchlm reference rather
  than re-measure. (Only Zen models incur API spend anyway.)
- Sandbox best-effort (not a hard boundary); rate limits (paid go Flash, backoff);
  prices re-verified at runtime where exposed.

## 14. Changelog v3 (round-2 traceability)
- **PM1 temperature contradiction** -> registry `omit_temp` is sole truth; profiles only
  for thinking controls; removed "temperature" from profile-reuse (§3/§5).
- **PM2/BM4 re-warm dimensional mismatch** -> compare two warm-up inference calls (§10).
- **BM3 warm-up sequencing** -> per-model, immediately before its batch (§10).
- **PM3 DeepSeek V4 Flash** -> reasoning:yes, omit_temp:yes (§3).
- **PM4 MiniMax M3 no action path** -> preflight live-test + loud-exclude contingency (§3/§10/§12).
- **PM5 Qwen 397b cost_model** -> resolved: subscription (Ollama Pro); metered = Zen only (§3/§8).
- **PM6 Hypothesis not installed** -> parameterised pytest, no new dep (§4).
- **PL1 ladder direction** -> weakest-first (ASC), Opus pinned last (§2/§9).
- **PL2 Kimi reasoning posture** -> server default, pin option documented (§3).
- **BM1 estimate thinking budget** -> thinking_budget_multiplier (default 4.0) (§8).
- **BM2 contamination threshold** -> floor(0.75*n_healthy), min 2 (§4).
### Carried from v2 (round-1, verified real by round-2 audit)
auth via env_loader (§5/§7); WAF UA (§5); subscription=$0-marginal + metered cap (§8);
explicit rank formula (§2); extractor.py (§5); Kimi->go routing (§3); ladder schema (§9);
validate-oracles gate (§4/§10); api_timeout (§3); cache-busting (§5); oracle isolation (§6);
reasoning/non-reasoning split + thinking_tokens (§2/§3/§5/§9); TTFT+ping (§5/§9); Wilson
CIs (§8/§9/§13); ToS framing (§13); error_type vs test-fail (§5).

## 15. Deferred (post-run-blocker conformance, tracked)
The #8 conformance re-audit confirmed the core sound and flagged ten non-run-blocking items.
Per an explicit decision, only the five run-blockers (A to E) were fixed; the following are
DEFERRED and recorded here so a future re-audit treats them as KNOWN, not new. A later
hardening pass then fixed four correctness findings ON the run-blocker fixes themselves
(M1 reasoning-guard substring false-positive, M2 reasoning_extras aliasing, M3 lost partials
on budget-stop, L1 default-bar mismatch) under plan->audit->TDD; only the cosmetic L2 below
was deferred from that pass.

**Should-fix (correctness / observability):**
1. **Persistence gaps:** the per (model,task) pytest output and the nonce'd prompt are not
   yet written to `runs/<id>/` (§9 lists them as the target).
2. **Served-model field not captured:** the response's served model id is not recorded, so a
   right-class / wrong-variant mis-route is undetectable.
3. **Cache-hit retry double-bills:** a sub-100ms cache hit is retried once; the retry's tokens
   are billed by the gateway but tracked once locally.
4. **Ceiling bar-failure warning:** when the pinned ceiling itself fails the bar there is no
   dedicated annotation. (Run-blocker C added a degenerate-ladder warning for a <= 1-entry
   ladder, which partially overlaps but does not cover the ceiling-specific case.)
5. **`env_loader` unused:** the run reads `BAKEOFF_GATEWAY_URL/KEY` directly rather than via
   the shared `env_loader` path (§5/§7).
6. **No `report` subcommand** to re-render `report.md` / `ladder.yaml` from a persisted run
   without re-spending.

**Low:**
1. `--ignore-glob` pattern breadth (model-authored test-collection edge cases).
2. TTFT is always None (not separately measured from total latency).
3. Cache-hit is flagged but not logged.
4. Ping-baseline label wording.
5. **L2 (deferred from the hardening pass):** the report table has no per-row marker for
   bar-excluded models; the Notes section lists the exclusions, so this is cosmetic only.
