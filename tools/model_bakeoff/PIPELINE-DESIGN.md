# Model Testing Pipeline, programme design

**Status:** approved (verbal, 2026-07-02). This is the north-star spec for a multi-increment
programme built on the existing `feat/model-bakeoff` harness (`tools.model_bakeoff`). Each
increment gets its own implementation plan + plan-audit loop before code.

## Goal
Evolve the one-shot coding bakeoff into a reusable **model testing pipeline**: modular,
swappable/chainable challenge suites; per-model **best-shot** settings so each model competes at
its true peak; and a published **case study** of the optimal settings and the head-to-head.

## Why (the finding that shapes this)
The n=3 equal-footing baseline (2026-07-02, `[[model-bakeoff]]` in brain) showed the raw ranking
is misleading: both DeepSeek reasoners made **zero wrong answers** yet scored 77%, because
deepseek-v4-pro timed out and deepseek-v4-flash truncated on the hardest tasks under uniform
settings (16k tokens / 240s). A first mutation test then showed opencode-go returns **503 at ~270s**
on long DeepSeek requests regardless of client timeout, so part of the handicap is the **provider**,
not just our settings. Conclusion: we must (a) give each model its best settings, and (b) measure
**operational reliability per provider** as its own axis, because it directly gates real usage.

## Ranking axes (5)
1. **Accuracy** — oracle pass-rate on *completed* runs + Wilson CI. Wrong answers only.
2. **Reliability** — operational failure rate per model AND per gateway: call-errors, gateway 503s,
   timeouts, empty-output truncations, extraction failures. NEW first-class axis (was conflated
   into accuracy, which mis-ranked the field). "Provider failure rate impacts usage."
3. **Elegance** — LLM judge (claude-sonnet on opencode-zen; the only metered call).
4. **Speed** — p50 latency, cache-hit-excluded.
5. **Cost** — sticker-price output-token proxy (candidates are subscription = $0 marginal).

## Methodology
- **Dual run:** every benchmark reports an **equal-footing baseline** (uniform settings, the
  controlled condition) AND a **best-shot** run (each model at its tuned settings). The case study
  reports the per-model **delta** tuning buys. Both on the same held-out scored corpus.
- **Best-shot tuning:** seed from researched priors (`[[coding-inference-settings]]`), then a cheap
  **hill-climb** on a HELD-OUT dev set, sampling (temperature/top_p/top_k), reasoning/thinking
  depth, max output tokens, and **gateway/provider** (where a model is served on >1). Objective =
  oracle pass-rate (no judge, so tuning is free/fast), tie-break fewer tokens / faster / more
  reliable. Repeat each candidate 2 to 3x for stochastic stability.
- **Leakage guard:** the dev set is DISJOINT from the scored corpus. Tuning on the scored tasks
  would just measure overfitting. Default: new dedicated dev tasks (spanning tiers, incl. >=1 AI-trap).
- **Persist everything** (standing rule): every sweep's outputs + the winning per-model settings
  record, durably, keyed for retrieval.

## Sub-projects and build order
- **A. Suites (Phase 1)** — modular challenge sets. Optional per-challenge `meta.yaml`
  (`tier`, `tags`); named YAML manifests under `suites/`; `corpus.load(selector)` resolves
  `None` (whole corpus, unchanged default) / `tag:<t>` / `<manifest-name>`; `--suite` on
  run/estimate/validate-oracles; new `validate-suites`. Fully backward compatible. Unblocks a clean
  dev/test split.
- **B. Reliability axis + error-aware reporting** — record `error_type` per run; add per-model and
  per-gateway operational-failure counts to `ModelAggregate`/summary/report; separate "completed
  accuracy" from "operational failures"; scoreboard shows an explicit reliability column. (Promotes
  the previously-deferred harness gap to a requirement; also the first thing that makes the dual-run
  interpretable.)
- **C. Settings override profiles + tuning driver** — run any model under a settings profile layered
  over its `ModelSpec` default (no roster edits); a sweep/hill-climb driver over the dev set; emit a
  per-model optimal-settings record. Provider is one of the swept knobs.
- **D. Dual-run benchmark** — run the full scored corpus under baseline AND best-shot, with judging;
  report both + the delta.
- **E. Case study** — published deliverable (default: Artifact + brain-wiki ingest): methodology,
  per-model optimal settings + rationale, baseline-vs-best-shot + reliability-by-provider, caveats.

## Non-goals (YAGNI / deferred)
- Phase 2 stateful multi-step challenges (output of step N feeds step N+1).
- Phase 3 dependency-graph chaining (prereqs / conditional execution).
- No new model onboarding beyond what a run needs.

## Key defaults (owner decisions; change on request)
- YAML for meta/manifests; `suites/` sibling to `tasks/`; tag syntax `tag:<t>`; manifests ordered,
  tag/all runs sorted.
- Tuning objective = oracle pass-rate (no judge); hill-climb not full grid; dev set = new dedicated tasks.
- Case study published as an Artifact + ingested to the brain wiki.

## Success criteria (programme)
- Suites: `--suite tag:ai-trap` and `--suite <name>` select correctly; default run unchanged; suite
  recorded in the report; `validate-suites` gates manifests. Full suite green.
- Reliability: an error-only or all-timeout model is never reported as a genuine 0; per-gateway
  failure rate surfaced; the DeepSeek baseline re-reads correctly (0 wrong answers, high op-failure).
- Best-shot: a persisted optimal-settings record per model; measurable, reproducible tuning deltas.
- Dual-run: baseline vs best-shot table + per-provider reliability, on a corpus disjoint from tuning.
- Case study: published + ingested, with every figure traceable to a run artefact.
