# Sub-project D: dual-run benchmark (equal-footing baseline vs best-shot), plan (REV5)

Fourth sub-project of the model-bakeoff programme (PIPELINE-DESIGN A->B->C->D->E). A->C are SHIPPED
(207 tests) and the first live tune is done (src-2026-07-02-05). Plan + dual-audit ONLY per the user's
sequence; implementation is a later step. TDD-structured so it is execution-ready.

REV5 changelog (both REV4 auditors verified all REV3 findings CLOSED and the core sound; these are the
remaining refinements, both auditors agreeing on the interface gaps):
- CRITICAL (blind-spot): `stochastic_bestshot` structurally cannot fire for the 5 omit_temp=True
  reasoning models, yet the wiki documents DeepSeek thinking-mode as sampling INTERNALLY regardless of
  the harness's temperature (vendor non-determinism), so a --repeats=1 DeepSeek accuracy flip could be
  pure sampling noise with no caveat. Add a broader `sampling_uncontrolled` disclosure.
- HIGH x2 (both): `_phase_metrics` needs the drift-corrected `spec` to compute cost_proxy_usd.
- HIGH (plan-auditor): `TaskMetric` needs a `pass_rate` field for the D6g flakiness signal.
- HIGH (blind-spot): the paired-intersection axes crash on `statistics.median([])` when both phases have
  data but a DISJOINT completed set (empty intersection); D6d guarded only whole-phase-zero.
- MED (blind-spot): `p50_paired_delta` should be median-of-per-task-differences, not difference-of-medians.
- MED/LOW (blind-spot): the corpus-power caveat must warn E against vote-counting across 2-3 models.
- LOW (plan-auditor): state `tuned_temperature = bestshot_spec.temperature`.

## Goal
Run the SCORED corpus (`tasks/`, 10 tasks, disjoint from the `dev_tasks/` the tuner trained on) TWICE
per model (equal-footing baseline vs C's tuned best-shot); report the per-model tuning DELTA on all 5
axes with a PROPER PAIRED significance test on every axis, honest noise disclosure (including
vendor-internal sampling non-determinism), leakage attestation, order-confound control, and
low-confidence propagation, in a way that cannot crash, fabricate a result, or read a within-noise /
composition-artefact / sampling-artefact change as a real finding for the published sub-project E.

## The methodology this plan MUST get right (both auditors' CRITICAL history)
Baseline and best-shot are evaluated on the IDENTICAL 10-task `tasks/` corpus per model: a PAIRED binary
design. Four noise/confound sources must not be reported as real effects:
1. **Small-corpus + wrong-test:** McNemar on per-task pass/fail FLIPS is PRIMARY (D6a); Wilson-CI overlap
   is DESCRIPTIVE secondary (D6c); `--repeats=1` default avoids pseudo-replication; the report states the
   corpus is small so most deltas are indistinguishable (E under-claims). VERIFIED fixed.
2. **Task-composition drift:** speed/cost/elegance are paired over the SAME completed-in-both intersection
   McNemar uses, not phase-level averages; `task_composition_mismatch` flags a set difference (D6b).
3. **Swept-sampling stochasticity:** a tuned winner with `omit_temp=False` may set a non-zero temperature
   (glm-5.2's winner = 0.7) while the baseline is temp 0 -> `stochastic_bestshot` (D6f).
4. **Vendor-internal stochasticity (blind-spot REV4 CRITICAL):** the 5 omit_temp=True reasoning models
   (deepseek-v4-flash/-pro, kimi-k2.6, qwen3.7-max-go, qwen3.5-397b) are documented in the project wiki
   (coding-inference-settings.md: "DeepSeek V4 thinking mode ignores sampling ... temperature ... have NO
   effect"; vendor temp 1.0; "avoid temp 0 in thinking") to SAMPLE INTERNALLY regardless of what the
   harness sends. So for these models BOTH phases are non-deterministic by vendor design, and at
   --repeats=1 an accuracy flip can be pure sampling noise. Since the wiki flags exactly these reasoners
   as D's headline story ("best-shot ... expected to surface their true accuracy"), the noise disclosure
   MUST cover them -> `sampling_uncontrolled` (D6f), independent of any temperature field.

## Why this is the right shape (consumes C, feeds E)
C's `best_settings.json` (schema_version 1) snapshots the full base `ModelSpec` AND the winner profile so
D reconstructs the tuned spec:
`apply_profile(ModelSpec(**rec["base"]), SettingsProfile(**{k:v for k,v in rec["winner"].items() if v is not None}))`
(verified across REV1-4 to round-trip; apply_profile raises on gateway-without-wire_id and
sampling-under-omit_temp; the tuner's gateway knob is a PAIRED replace). D evaluates on `tasks/`; C tuned
on `dev_tasks/`. E consumes D's dual report.

## Key design decisions

### D1 New `dualrun` CLI command, orchestrated as per-(model, phase) interleaved runs (crash-safe)
dualrun loops the selected models; for each model it runs baseline and best-shot as TWO single-model
`run_bakeoff` calls, INTERLEAVED per model (baseline-A, bestshot-A, baseline-B, ...). Hardening:
- **Empty-result guard:** run_bakeoff SKIPS a model whose gateway is unconfigured (cli.py:332-335 ->
  `aggregates=[]` -> `rank.assemble([])` returns `report_rows=[]`). After each call `if not
  result.report_rows:` record a loud note, mark the phase no-data, exclude that model's delta row,
  continue - never crash.
- **Interior `ceiling_on=False`:** suppresses the shipped "ceiling requested" WARNING (cli.py:387-395) on
  each single-model call. `--no-ceiling` controls whether the ceiling MODEL is in the roster (moot under
  the subscription default).
- **Incremental combined-report persistence:** `dualrun.md`/`dualrun_summary.json` written after each
  model's delta (mirroring tuning.py), so a crash on model N never discards 1..N-1.
- **Shared budget by SUBTRACTION:** run_bakeoff builds its own `BudgetTracker` (cli.py:314) but returns
  `(result, spent)`; dualrun keeps `spent_so_far`; each call gets `budget_usd = max(0.0, total -
  spent_so_far)`; then `spent_so_far += spent`.
- Cross-model contamination detection is the standard `run`'s job (non-goal).

### D2 Tuned-spec loader `settings_loader.py` (+ subscription-default model selection)
`load_tuned_specs(settings_dir, roster, env=None) -> (specs_by_key, notes)`: reconstruct via apply_profile
when `<settings_dir>/<key>/best_settings.json` exists; else fall back to the roster spec + a loud note.
Default `--models` is the SUBSCRIPTION subset `[m for m in ROSTER if not m.is_metered]`; the three metered
models are unconditionally skipped by C's tuner so they can never have a tuned record. A metered key
passed explicitly runs with a loud note AND inherits the budget-edge guards. NOTE: a reconstructed winner
with omit_temp=False may enable sampling (glm-5.2 = 0.7 -> stochastic best-shot, D6f); the omit_temp=True
reasoning models cannot carry a temperature at all but are vendor-internally stochastic anyway (D6f
`sampling_uncontrolled`).

### D3 Base-drift guard (fail loud, raise-safe)
Assert `ModelSpec(**rec["base"]) == registry.by_key(key)`; on mismatch WARN + use the CURRENT roster spec.
If the drift-corrected reconstruction itself raises ValueError (roster now omit_temp=True but winner
carries a temperature), catch it, WARN, fall back to the baseline spec for BOTH phases. All drift recorded.
The final reconstructed bestshot ModelSpec that survives D3 is what D6/`_phase_metrics`/`tuning_delta` use
downstream (single source of truth for that model's price + effective temperature).

### D4 Leakage attestation (fail loud), via a NEW cross-dir helper with a dir-absent fallback
`corpus.assert_disjoint_dirs(dir_a: str, dir_b: str | None = None, *, ids_b: set[str] | None = None) -> None`
- `dir_b` a real dir: load both dirs' task_ids, raise on any shared id; `dir_b=None, ids_b` given: check
  dir_a's ids against the set (dev dir absent, ids from `dev_corpus.json["dev_tasks"]`); both None: raise.
D asserts (a) STRUCTURAL (different dirs; record both paths); (b) HARD ID CHECK (exit 2 loud on overlap);
(c) PROVENANCE (record `dev_corpus.json` = `{tree_sha, oracle_ref_sha256, dev_tasks:[id,...]}`). Both
absent -> WARN, do not block.

### D5 Metered-judge budget + per-phase judge control
- `--budget` is the TOTAL cap across BOTH phases (shared via D1 subtraction).
- `--elegance {both,bestshot,none}` (default `bestshot`): judge only best-shot by default; baseline
  elegance renders "baseline unjudged (policy)".
- Mechanism: NEW backward-compatible `run_bakeoff(..., judge_enabled: bool = True)` (Task 7); when False,
  skip Phase 2 AND the upfront self-grade guard + `gateways.resolve(jspec.gateway)` (cli.py:322-326) for a
  GENUINE no-op. Default True => shipped callers/tests byte-identical.
- `--judge <key>` accepted like `cmd_run`.
- Judge-coverage transparency: DeltaRow carries `baseline_n_elegance_judged`/`bestshot_n_elegance_judged`
  (models.py:159); the renderer distinguishes "baseline unjudged (policy)" from "partial judge coverage
  (n=X of Y; shared budget likely exhausted by this point in the run order)".

### D6 Delta computation: paired on EVERY axis
Per-task data for BOTH phases comes from a `cli` helper reading run_bakeoff's persisted raw:
`_phase_metrics(raw_dir: str, repeats: int, spec: ModelSpec) -> dict[str, TaskMetric]` (TaskMetric = frozen
dataclass {passed: bool, latency_s: float|None, cost_proxy_usd: float, elegance: float|None,
pass_rate: float}). The `spec` param is REQUIRED (both auditors' HIGH): cost_proxy_usd is computed via
`client.cost_proxy_usd(spec, completion_t, thinking_t)` (client.py:89-95, needs spec.price_out_per_m,
which is NOT in the raw JSON), and it MUST be the SAME drift-corrected spec D3 produced for this model
(not a fresh registry.by_key lookup, which could diverge). CONTRACT (pin exactly):
- group raw `<key>__<task>__r<rep>.json` files (fields task, passed, error_type, latency_s, cost_usd,
  completion_tokens, thinking_tokens, elegance; cli.py:172-188) by task_id;
- a task_id is INCLUDED (a key) ONLY IF it has EXACTLY `repeats` files AND NONE is operational
  (`error_type in OPERATIONAL_ERROR_TYPES`, models.py:28). An operational repeat OR a partial repeat count
  (budget truncation) OMITS the key ENTIRELY (never a `False`), so paired_significance's presence-based
  pairing excludes it. (Closes both REV3 HIGHs.)
- for an included task: `passed = all repeats passed`; `pass_rate = passed_repeats/repeats` (for D6g
  flakiness); `latency_s = median of the repeats' latencies` (None if all cache-hit-excluded);
  `cost_proxy_usd` = sum over the task's repeats of client.cost_proxy_usd(spec, completion_t, thinking_t)
  divided by repeats; `elegance` = mean of the repeats' judged elegance (None if none judged).

D6a - PRIMARY significance (paired, McNemar exact):
`rank.paired_significance(baseline_passes: dict[str,bool], bestshot_passes: dict[str,bool]) ->
PairedResult(n_paired, b, c, p_value, significant)`: pairs task_ids in BOTH maps; `b` = passed-baseline &
failed-bestshot, `c` = failed-baseline & passed-bestshot; exact two-sided McNemar
`p = min(1.0, 2*sum(math.comb(n,i) for i in 0..min(b,c)) * 0.5**n)`, `n=b+c` (n=0 -> p=1.0);
`significant = p < 0.05`. Pure math.comb. (Hand-verified correct.)

D6b - the delta axes, ALL paired over the SAME completed-in-both intersection:
- accuracy: `*_pass_fraction`/`pass_fraction_delta` over the intersection; `*_completed`/`completed_delta`.
- reliability: `*_n_operational`/`n_operational_delta` from the phase aggregates (inherently corpus-wide,
  labelled - operational tasks are excluded from the intersection by construction, so pairing reliability
  over the intersection would trivially be zero; blind-spot verified this is the correct call).
- speed: `p50_paired_delta = median(bestshot_metrics[t].latency_s - baseline_metrics[t].latency_s for t in
  intersection where BOTH latencies are non-None)` - MEDIAN OF PER-TASK DIFFERENCES, not
  difference-of-medians (blind-spot MED: median is non-linear; only per-task-differences is genuinely
  paired). None if the filtered list is empty.
- cost: `cost_proxy_paired_delta = mean over the intersection of (bestshot cost_proxy_usd - baseline
  cost_proxy_usd)` (mean is linear so per-task-diff == diff-of-means); AND `cost_usd_delta` (real $,
  meaningful only for a metered candidate).
- elegance: `elegance_paired_delta` = mean over the intersection of per-task elegance differences where
  both non-None (None if empty).
- The phase-level ModelAggregate p50/cost/elegance are recorded for context, LABELLED "corpus-wide, not
  per-task-paired". `task_composition_mismatch = set(baseline_metrics) != set(bestshot_metrics)` -> tag.

D6c - DESCRIPTIVE secondary significance: `ci_overlap = rank._overlap(baseline, bestshot)` DIRECTLY (CIs
set in place by each phase's assemble(), cli.py:397 / rank.py:81-82). Rendered secondary, NOT the verdict.

D6d - zero/mismatch/empty guards (crash-safety - the plan must NEVER crash):
- `if baseline.n_tasks == 0 or bestshot.n_tasks == 0`: NO numeric delta; `no_data=True`, reason
  "phase produced zero completed tasks"; never a degenerate wilson_ci(0,0).
- **EMPTY-INTERSECTION guard (blind-spot HIGH):** BEFORE any median/mean over the intersection, if the
  intersection `set(baseline_metrics) & set(bestshot_metrics)` is empty, OR the non-None filtered list for
  a given axis is empty, set THAT axis's paired delta to None with a note "no paired data on this axis
  (empty task-composition intersection)" - never call `statistics.median([])`/mean on an empty sequence.
  Note: two phases can BOTH have n_tasks>0 yet a disjoint completed set (asymmetric operational/budget
  truncation), which the whole-phase-zero guard alone does not cover.
- `n_tasks_mismatch = baseline.n_tasks != bestshot.n_tasks` (count) AND `task_composition_mismatch` (set)
  both surfaced.

D6e - causal annotations:
- `gateway_capped = same gateway AND baseline.n_operational > 0 AND bestshot.n_operational >=
  baseline.n_operational` -> "reliability gap persists on the SAME gateway; tuned settings cannot fix a
  gateway ceiling". The `baseline.n_operational > 0` clause prevents mislabelling a baseline-clean case.
- `tuning_induced_regression = same gateway AND baseline.n_operational == 0 AND bestshot.n_operational > 0`
  -> "tuned settings INTRODUCED operational failures (regression), not a pre-existing ceiling". (Detects a
  real family failure mode - the glm-5.2 search trace shows a REJECTED neighbour going 0->3 operational;
  the shipped winner shows n_operational=0, so this does not fire on today's artefact.)

D6f - stochasticity disclosure (two independent flags):
- `stochastic_bestshot = (tuned_temperature not in (None, 0)) AND repeats == 1`, where `tuned_temperature
  = bestshot_spec.temperature` (raw field, safe: apply_profile/D3 exclude the incoherent omit_temp+temp
  case). Annotates "best-shot enables sampling temperature=X; a single-draw flip may be sampling noise".
- `sampling_uncontrolled = baseline_spec.omit_temp OR bestshot_spec.omit_temp OR baseline_spec.reasoning
  OR bestshot_spec.reasoning` (blind-spot CRITICAL) -> fires INDEPENDENT of the temperature field,
  covering the 5 omit_temp=True reasoners. TWO-TIER caveat wording (blind-spot REV5 MED - do not overclaim
  provenance in publication-facing text): for a model with an actual wiki citation (currently
  deepseek-v4-pro / deepseek-v4-flash, coding-inference-settings.md: "thinking mode ignores sampling ...
  NO effect") render "sampling is server-controlled and VENDOR-DOCUMENTED non-deterministic in thinking
  mode (see coding-inference-settings.md); a single-draw flip may be pure vendor-internal sampling noise";
  for the remaining omit_temp/reasoning models WITHOUT a specific citation (kimi-k2.6, qwen3.7-max-go, and
  qwen3.5-397b whose note is only "avoid temp 0 in thinking") render "reasoning-model sampling is commonly
  server-controlled and is UNDOCUMENTED for this model; treat as a heuristic caution, not a confirmed
  vendor fact". Both tiers recommend --repeats>=3; the distinction preserves the caution without
  overstating the evidentiary basis to E. The plan carries a small cited-set constant (initially
  {deepseek-v4-pro, deepseek-v4-flash}) that Task 6 keys the tier on.
- When EITHER flag is set AND repeats == 1, the row and `estimate --dualrun`'s guidance recommend
  `--repeats >= 3` for that model.

D6g - flakiness: `dualrun_summary.json` records each phase's per-task `pass_rate` (e.g. 2/3) alongside the
binary collapse, so E can report flakiness reduction (1/3 -> 3/3) even when the binary verdict is unmoved.

D6h - disagreement note: when `ci_overlap` disagrees with `significant`, the renderer appends
"(CI-overlap and the paired test disagree at n=<n_paired>; the paired McNemar p-value is authoritative)".

### D7 Low-confidence propagation + run-level banner
Each row carries the tuned record's CANONICAL `low_confidence`+`reasons` (patch overwrites canonical;
verified live). Row annotated "tuned settings low-confidence (<reasons>); treat delta as indicative".
Run-level banner (fires on current data - both live records low_confidence:true): if
`n_low_confidence / n_models_with_tuned_records > 0.5`, PREPEND a WARNING banner naming the models.

### D8 Order-confound control + reproducibility/persistence
- `--order {alternate,baseline-first,bestshot-first}` (default `alternate`): even-indexed models
  baseline-first, odd-indexed best-shot-first. STATED SCOPE: `alternate` protects the FLEET aggregate, NOT
  an individual model's back-to-back pair (that is `order_confound_suspect`).
- `order_confound_suspect` (DeltaRow field, GATEWAY-AGNOSTIC): `(n_operational_delta != 0) OR (baseline.p50
  is not None AND bestshot.p50 is not None AND (p50_ratio < 0.5 OR > 2.0))`, with a None-p50 guard.
  Annotated as a caution + names a gateway change if any. RESIDUAL LIMITATION stated in the renderer: with
  no within-model order repetition this cannot fully separate an artefact from a real effect.
- Persistence: each (model, phase) keeps full artefacts under `runs/<ts>-dualrun/<model>/<phase>/`; the
  combined report at the run root is written incrementally.

## Interfaces (new / changed)
- NEW `settings_loader.load_tuned_specs(settings_dir, roster, env=None) -> (dict[str,ModelSpec], list[str])`
- NEW `corpus.assert_disjoint_dirs(dir_a: str, dir_b: str | None = None, *, ids_b: set[str] | None = None) -> None`
- NEW `rank.paired_significance(baseline_passes: dict[str,bool], bestshot_passes: dict[str,bool]) -> PairedResult`
- NEW `cli._phase_metrics(raw_dir: str, repeats: int, spec: ModelSpec) -> dict[str, TaskMetric]`  (TaskMetric = frozen {passed, latency_s, cost_proxy_usd, elegance, pass_rate}; OMIT operational/partial)
- NEW `rank.tuning_delta(baseline: ModelAggregate, bestshot: ModelAggregate, baseline_metrics: dict[str,TaskMetric], bestshot_metrics: dict[str,TaskMetric], baseline_spec: ModelSpec, bestshot_spec: ModelSpec, repeats: int) -> DeltaRow`  (DeltaRow computes paired_significance + paired per-axis deltas + all flags incl. sampling_uncontrolled/stochastic_bestshot)
- NEW `report.render_dualrun_md(...) -> str`; `report.render_dualrun_summary_json(...) -> str`
- CHANGED `cli.run_bakeoff(..., judge_enabled: bool = True)`  (backward-compatible; when False also skips the self-grade guard + judge gateway resolve)
- NEW `cli.cmd_dualrun(args, env=None, transport=None) -> int`; `dualrun` subparser
  (`--models --suite --settings-dir(required) --dev-tasks-dir --budget --elegance {both,bestshot,none}
    --order {alternate,baseline-first,bestshot-first} --judge --no-ceiling --repeats(default 1) --bar
    --sandbox-timeout --out`).
- CHANGED `cli.cmd_estimate` gains `--dualrun` (+ `--elegance`).

## Tasks (TDD; each ends green + a commit)

### Task 1: `settings_loader.load_tuned_specs` + base-drift guard (raise-safe)
- Files: create `settings_loader.py`; test `tests/test_settings_loader.py`.
- Tests: (a) record+winner reconstructs apply_profile; (b) missing record -> baseline + note; (c)
  base-drift -> WARNING + current roster spec; (d) winner all-None -> baseline; (e) drift-corrected
  reconstruction raises -> caught, loud note, baseline fallback both phases. Hand-written fixtures.
- Commit: `feat(bakeoff): settings_loader reconstructs tuned specs from best_settings.json`.

### Task 2: `corpus.assert_disjoint_dirs` (dir-pair AND ids-fallback) + tests
- Files: modify `corpus.py`; test `tests/test_corpus.py`.
- Tests: (a) disjoint dirs pass; (b) shared id -> ValueError naming it; (c) `dir_b=None, ids_b={...}`
  overlap caught; (d) both None -> ValueError.
- Commit: `feat(bakeoff): corpus.assert_disjoint_dirs cross-directory leakage check with id-list fallback`.

### Task 3: `rank.paired_significance` (McNemar exact) + `PairedResult`
- Files: modify `rank.py`; test `tests/test_rank.py`.
- Tests: (a) b=c=0 -> p=1.0 not significant; (b) b=0,c=8 -> p<0.05 significant; (c) b=4,c=4 -> p~1.0; (d)
  only task_ids present in BOTH maps are paired (assert n_paired excludes a one-sided key); (e) exact value
  vs a hand-computed small binomial. Pure math.comb.
- Commit: `feat(bakeoff): rank.paired_significance (exact McNemar on per-task flips)`.

### Task 4: `cli._phase_metrics` + `TaskMetric` (raw -> per-task, contract-pinned, spec-priced)
- Files: modify `cli.py` (+ `TaskMetric`); test `tests/test_cli.py`.
- Tests (write raw fixtures with the real _persist_raw schema into a tmp raw dir; pass a ModelSpec with a
  known price_out_per_m): (a) EXACTLY `repeats` all-passing non-operational files -> key present,
  passed=True, pass_rate=1.0, latency=median, cost_proxy_usd == the expected client.cost_proxy_usd(spec,
  ...) value (assert the number, using the passed spec's price); (b) an OPERATIONAL repeat (call_error) ->
  key ABSENT (not False); (c) FEWER than `repeats` files -> key ABSENT; (d) `repeats` files with one
  non-operational failure -> key present, passed=False, pass_rate<1; (e) median latency + pass_rate
  correct. This is the dedicated unit test both auditors asked for.
- Commit: `feat(bakeoff): _phase_metrics reconstructs contract-pinned, spec-priced per-task metrics from raw`.

### Task 5: `rank.tuning_delta` + `DeltaRow` (paired axes + flags + empty-intersection safety)
- Files: modify `rank.py`; test `tests/test_rank.py`.
- Tests: paired accuracy (via paired_significance); paired speed as MEDIAN OF PER-TASK DIFFERENCES (assert
  it differs from difference-of-medians on a constructed case), paired cost/elegance over the intersection;
  None-safety (all-operational -> completed None; unjudged -> elegance None; None p50); EMPTY-INTERSECTION
  (disjoint completed sets, both n_tasks>0) -> paired axes None with a note, NO median([]) crash; `no_data`
  when either n_tasks==0; `n_tasks_mismatch` (count) and `task_composition_mismatch` (set) correct;
  `ci_overlap` via rank._overlap; `gateway_capped` True only when same gateway + baseline.n_operational>0;
  `tuning_induced_regression` True when baseline op=0 & bestshot op>0 same gateway; `order_confound_suspect`
  gateway-agnostic + None-p50 safe; `stochastic_bestshot` True when bestshot_spec.temperature not in
  (None,0) and repeats==1; `sampling_uncontrolled` True when either spec is omit_temp or reasoning (assert
  it fires for a deepseek-like omit_temp=True spec where stochastic_bestshot does NOT). Carries b/c/n_paired.
- Commit: `feat(bakeoff): rank.tuning_delta - fully-paired axes, significance, empty-safe, noise/causal flags`.

### Task 6: dualrun report renderers
- Files: modify `report.py`; test `tests/test_report.py`.
- Tests: `render_dualrun_md` shows a per-model table with the PAIRED p-value AND raw `n_paired, b, c` as
  PRIMARY (e.g. "7/10 paired, 0 regressed / 5 improved, p=0.06 (not significant at n=7)") + a min-
  discordant-for-significance fact for the run's n; CI-overlap as labelled DESCRIPTIVE secondary with the
  D6h disagreement note; "elegance: baseline unjudged (--elegance bestshot policy)" distinct from
  "partial judge coverage (n=X of Y)"; non-accuracy deltas labelled paired-intersection with corpus-wide
  aggregates marked "context only"; a cost_proxy caption "token-volume x sticker price, USD-equivalent,
  NOT real spend"; the `stochastic_bestshot` AND `sampling_uncontrolled` caveats (the latter in TWO TIERS - assert a
  VENDOR-DOCUMENTED wording with a coding-inference-settings.md reference for a cited-set model, e.g.
  deepseek-v4-flash, and a "heuristic caution, UNDOCUMENTED for this model" wording for a non-cited
  reasoner, e.g. kimi-k2.6; both naming the --repeats>=3 recommendation); leakage/provenance + drift blocks;
  per-row low-confidence + gateway_capped + tuning_induced_regression + order_confound (with the residual-
  limitation line) + no_data + n_tasks_mismatch + task_composition_mismatch + empty-intersection
  annotations; the run-level low-confidence banner at >50%; and a corpus-size/power caveat that ALSO states
  "N_tuned_models=<n>; do not vote-count improved/regressed rows across models as evidence of a general
  tuning effect without accounting for per-row and cross-model multiplicity" (blind-spot MED/LOW).
  `render_dualrun_summary_json` records deltas + paired stats + per-task pass-rates + ci_overlap +
  provenance + order + notes.
- Commit: `feat(bakeoff): dual-run report + summary renderers (paired significance, noise disclosure, flakiness)`.

### Task 7: `run_bakeoff` `judge_enabled` param (backward-compatible, genuine no-op when False)
- Files: modify `cli.py`; test `tests/test_cli.py`.
- Tests: (a) REGRESSION default True -> Phase 2 runs (existing judged-run test green; judge call observed);
  (b) False -> Phase 2 skipped, NO judge call, NO self-grade ValueError with a same-family judge, note
  "elegance skipped (phase judging disabled)", non-elegance outputs unchanged.
- Commit: `feat(bakeoff): run_bakeoff gains an optional judge_enabled switch (default-inert, no-op when off)`.

### Task 8: `cmd_dualrun` orchestration (interleaved, subtraction budget, guards, leakage)
- Files: modify `cli.py`; test `tests/test_cli.py`.
- Tests (offline, injected transport, observable-behaviour idiom): (a) happy path - both phases per model,
  per-(model,phase) subdirs, dualrun.md/json written incrementally, paired p-value present; (b)
  `--elegance bestshot` judges only best-shot, spend ~half of `both`; (c) leakage - a scored suite sharing
  a task_id with the dev corpus -> exit 2 loud (and the dev-dir-absent path uses dev_corpus.json ids); (d)
  no-tuned-record model -> baseline both phases + note, delta ~0; (e) shared `--budget` (subtraction) - a
  cost-controlled transport drives a best-shot budget stop; assert TOTAL spend <= budget, truncation note
  present, truncated row `no_data`/composition-tagged (NOT a fake significant 0%); (f) default `--models`
  excludes the 3 metered; explicit metered key prints the loud note; (g) UNCONFIGURED GATEWAY phase ->
  empty report_rows -> batch does NOT crash, model excluded with a note, later models still run, combined
  report still written; (h) `--order alternate` records per-model phase order, even/odd start differently,
  N=1 no off-by-one.
- Commit: `feat(bakeoff): dualrun CLI (interleaved baseline vs best-shot, shared budget, leakage, crash-safe)`.

### Task 9: estimate dualrun mode
- Files: modify `cli.cmd_estimate` + subparser; test test_cli.py.
- Tests: `estimate --dualrun` prints SEPARATE line items (i) judge spend x2 under `both`, x1 `bestshot`, x0
  `none`; (ii) metered-candidate spend x2 (nonzero only if a metered key is selected) naming any selected
  model with no tuned settings; and a --repeats guidance note keyed off `sampling_uncontrolled`/
  `stochastic_bestshot` (recommend >=3 for affected models). Assert each case's numbers.
- Commit: `feat(bakeoff): estimate --dualrun surfaces the doubled judge + metered-candidate spend`.

## Rollback
Additive EXCEPT run_bakeoff's one new optional `judge_enabled=True` param, backward compatible (default
preserves today's behaviour; verified against all 7 test call sites). Each task is one revertable commit.
No live run in this sub-project's tests (injected transport).

## Success criteria
- `_phase_metrics` OMITS operational/partial tasks (never False), prices cost_proxy via the threaded
  drift-corrected spec, carries pass_rate; dedicated unit tests.
- `cmd_dualrun` runs the scored corpus twice per model (interleaved, counterbalanced), computes per-model
  deltas on all 5 axes PAIRED over the completed-in-both intersection (speed as median-of-per-task-diffs)
  with a McNemar verdict (+ raw b/c/n_paired + descriptive CI-overlap), discloses BOTH swept-sampling and
  vendor-internal (`sampling_uncontrolled`) stochasticity and task-composition mismatch, NEVER crashes
  (unconfigured gateway, zero-task phase, OR empty task intersection), and NEVER fabricates a significant
  0%; writes a crash-safe dual report + summary with leakage + provenance + low-confidence (per-row +
  banner) + drift + gateway_capped/regression + order-confound + coverage + flakiness + corpus-power +
  cross-model-multiplicity caveats.
- Metered judge spend bounded by a SHARED `--budget` (subtraction), visible via `estimate --dualrun`;
  `--elegance bestshot` halves it; metered candidates excluded by default.
- Leakage impossible-by-construction AND attested AND hard-checked, loud exit on overlap.
- run_bakeoff's judge switch default-inert with a green regression test; genuine no-op when off.
- Full suite stays green; shipped paths behaviourally unchanged.

## Explicit non-goals
- IMPLEMENTATION (plan + dual-audit only, per the user's sequence).
- The published case-study Artifact (sub-project E), including E's own cross-model synthesis methodology
  (D only DISCLOSES the multiplicity caveat; it does not perform the synthesis).
- Re-tuning or changing sub-project C's tuner. Editing the roster.
- Cross-model contamination detection within a phase (the standard `run`'s job).
- A live dual run (a later, separately budget-gated execution step).
