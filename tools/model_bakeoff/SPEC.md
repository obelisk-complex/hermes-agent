# Model Bakeoff — spec & plan (v3, post-audit round 2)

Date: 2026-06-29. Repo: hermes-agent fork, branch `feat/model-bakeoff`.
Status: PLAN v3 — round-1 (major) and round-2 (narrow) findings folded in. Round 2
returned 0 CRITICAL / 0 HIGH from both auditors. Awaiting re-audit PASS before code.

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
- **Contamination detection (B-M/PM):** after a run, flag any task where
  **>= floor(0.75 * n_healthy) (min 2)** models score perfect — a fraction, not a fixed
  count, so it still fires when models fail preflight. Flagged tasks -> manual review /
  exclusion from ladder derivation.

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
- `sandbox.py` — runs extracted code vs oracle in an isolated subprocess (§6).
- `scorer.py` — parses pytest; distinguishes collection/import/syntax **error**
  (non-zero exit, no results) from genuine test failures -> `error_type`.
- `runner.py` — orchestrates roster x corpus (N default 1); per-gateway ping baseline;
  **per-model warm-up immediately before that model's batch** (§10); budget enforcement;
  writes all artifacts.
- `rank.py` — §2 formula -> report (Wilson 95% CIs) + `ladder.yaml` (weakest-first, §2/§9).
- `cli.py` — `validate-oracles`, `preflight`, `estimate`, `run`, `report`.

## 6. Security — untrusted model code (CRITICAL)
Separate subprocess; fresh temp dir; `env` scrubbed to a minimal allowlist (NO
.env/auth/token vars); process-group kill on hard timeout; output size-capped. **Oracle
isolation:** oracle test file OUTSIDE any model-reachable dir; pytest `--import-mode=
importlib`, rootdir outside the writable temp dir, `--ignore-glob` for model-authored
`test_*.py` so only the oracle is collected. Best-effort network denial; documented as
NOT a hard boundary (no root on WSL); tasks need no net/fs, so a solution touching either
is itself flagged.

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
- `run` enforces a HARD cap (default $10) on Zen cost; abort loud if exceeded (partials
  persisted). Per-model `max_tok` bounds worst-case completion.

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
