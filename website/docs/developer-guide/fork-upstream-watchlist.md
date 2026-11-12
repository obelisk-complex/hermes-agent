# Fork upstream watchlist

**Purpose:** fork-local fixes to re-check against `NousResearch/hermes-agent`
before (or right after) the next `sync-upstream.yml` rebase/fast-forward. If
upstream has since landed an equivalent fix, ours is redundant weight in
every future rebase's diff and a future rerere-conflict candidate — drop
ours and adopt upstream's, *unless* upstream's version conflicts with the
fork's own intent (noted per entry below where that applies).

This is a checklist to walk, not an automated gate. When you (or an agent)
picks this up before/after a sync, open each `Upstream check` link, see if
the underlying issue is fixed there, and update the entry's status.

## How to use this

For each entry: check whether upstream's `main` now contains an equivalent
fix (search their commit history / the linked file for the same symptom).

- **Not yet upstream** → keep ours, re-check next time.
- **Upstream fixed it, compatible with fork intent** → drop the fork's
  patch, take upstream's rebase result as-is, remove the entry.
- **Upstream fixed it differently, incompatible with fork intent** → keep
  the fork's version; note *why* below so the next check doesn't waste time
  re-litigating it.

---

## Entries

- **MCP readOnlyHint lookup** (`tools/mcp_tool.py`, keyed via
  `_resolve_server_key` since 2026-10-03). Its only caller,
  `tools/mcp_tool_sampling.py`, runs in the MCP receive loop, which does not
  inherit context variables, so in a multiplexed gateway the scope may still
  resolve to none. Unverified. Upstream's elicitation path is unchanged
  (`_pending_call_context` replay only wraps the consent call, not the hint
  lookup), so this stays open. Re-checked 2026-10-28: `upstream/main` still has
  no `mcp_tool_read_only_hint` or caller of the hint in `mcp_tool_sampling.py`.
  Re-checked 2026-11-11: unchanged.
- **`install_modify_other_keys_aliases()` also installs the keypress data
  normalisation** (`hermes_cli/pt_input_extras.py`). Upstream calls
  `install_keypress_data_normalization()` only from `cli.py`; the curses pickers
  (`hermes_cli/curses_ui.py`) install the aliases alone, so they miss the
  normalisation the fork adds. Drop once upstream's `install_modify_other_keys_aliases()` (or
  `curses_ui.py`) installs the normalisation itself. Re-checked 2026-10-28 and
  2026-11-11: unchanged upstream.
- **Upstream's code-health ratchet vs a sync PR** (`.github/workflows/lint.yml`,
  `scripts/code_health`, `ENFORCEMENT = "blocking"`, added since 2026-10-03). On a
  pull request it compares the merge commit with its first parent, which for a
  sync PR is fork `main`, so every upstream-side file or function that grew since
  the last sync counts as a regression (89 blocking findings on the 2026-10-05
  sync). Against `upstream/main` the fork's own code still has 40 (mostly `BLE001`
  blind `except Exception` in `plugins/quality-gate`, `plugins/self-check-enforcer`,
  `tools/approval*.py`, `hermes_cli/approval_mode.py`, `hermes_cli/kanban_db.py`,
  plus `FILE_LINES` on the files the fork extends). `sync-upstream.yml`'s pre-push
  gate does not run it; only a manually opened sync PR does. Undecided: flip
  `ENFORCEMENT` to `"advisory"` on the fork, or clean the 40. Re-checked
  2026-10-28: `upstream/main` is still `ENFORCEMENT = "blocking"`; counts above not
  re-measured. Re-checked 2026-11-11: still `"blocking"`.
- **Workflow runners and the JS check matrix** (`desktop-bundle-smoke.yml`,
  `install-e2e-windows-run.yml`, `js-tests.yml`). Upstream moved these to larger
  runners (`windows-latest-32-core`, `blacksmith-32vcpu-ubuntu-2404`) that never
  resolve on a personal account, so the fork keeps the standard runners. `js-tests.yml`
  keeps the fork's per-package matrix on `ubuntu-latest` and takes upstream's
  "Prepare locked Electron runtime" step, but not its `run-workspace-checks.mjs`
  step. Re-check each sync: runner labels and the step list. Since 2026-11-11 the
  script itself is kept (upstream's `just check-js-ci` runs it locally); only the
  fork's CI does not call it. `.github/actionlint.yaml` (upstream's Blacksmith
  runner-label list) stays deleted.
- **npm to pnpm** (upstream `7062229090`, taken 2026-11-11). The fork's
  `js-tests.yml` matrix discovery now installs with `pnpm install --frozen-lockfile
  --ignore-scripts` and lists packages with `pnpm ls -r --depth -1 --json` (the rule
  `run-workspace-checks.mjs` uses); each leg runs `pnpm --dir <pkg> run <script>`.
  `apps/desktop/scripts/run-ui-shard.mjs` delegates to `pnpm run test:ui`. Neither
  was run on a real runner before the sync PR; check the first `js-tests` run.
- **`block_task` and the fork's `fork_kanban_task_blocked` hook**
  (`hermes_cli/kanban_db.py`). Upstream `d4283a9c9c` dropped the dependency lane's
  in-transaction early return, so upstream's `kanban_task_blocked` now fires
  post-commit for every kind. The fork hook still skips the dependency lane (it parks
  in `todo`, not `blocked`), via `fire_fork_blocked_hook`. Revisit if upstream gives
  the dependency lane its own event.
- **Dual-signal tags on a multi-key prompt** (`tools/approval.py`). Upstream now asks
  once for every pending pattern key (`detect_content_rule_keys`, `_pending_findings`).
  The fork passes one `warnings` tuple per key to `_resolve_tags`, so auto-approval
  needs every key's tag enabled; a content-rule key with no taxonomy entry resolves
  `UNTAGGED` and falls through to the human. Re-check if upstream changes how keys
  reach `_human_decision`.
- **`tui_gateway/slash_worker.py` `env_float` import**. Upstream removed it with the
  env-tunable watchdog; the fork re-adds it for
  `HERMES_SLASH_WORKER_MCP_DISCOVERY_TIMEOUT_S`. Drop the fork's join bound if
  upstream raises the slash worker's discovery wait itself.
- **Fork-owned debt the sync gate trips on** (both pre-date 2026-11-11 and are
  identical on `origin/main` `b277306cb0`). `ruff check .` reports 92 findings, all in
  fork files (`plugins/quality-gate`, fork tests, `tools/action_tags.py`, ...), so
  `sync-upstream.yml`'s ruff step fails; upstream's own tree is clean.
  `tests/gateway/test_kanban_recovery.py` (`custom`, `restart`) fails on the fork but
  passes on upstream: the mandatory quality-gate plugin's tier classification times
  out and a `pre_kanban_spawn` model override applies inside upstream's recovery
  probe. Neither is in the fork-local test set the gate runs.

### Resolved 2026-11-11 (kept for history)

- **`qs: 6.16.0` override** (`scripts/whatsapp-bridge/package.json`): upstream's
  bridge lock resolves 6.16.1 with `body-parser` 1.20.9. Override dropped.
- **`sync-upstream.yml` dependency install**: asked for the `dev` and `hindsight`
  extras upstream no longer defines; now mirrors `tests.yml` (`--group dev`,
  `keychain`). Its marker scan also skips upstream's
  `tests/test_audit_old_updater_imports.py` fixture, which holds literal markers.

### Resolved 2026-10-28 (kept for history)

- **electron 41.10.3 pin**: upstream is on 44.9.3, past the fork's CVE floor.
  Pin dropped.
- **`@xmldom/xmldom` overrides**: upstream's lock resolves 0.8.15 and 0.9.12.
  Overrides dropped.
- **`qs: 6.16.0` override** (`website/package.json`) and the `qs` min-release-age
  exclude (`website/.npmrc`): upstream's lock is at 6.16.0 and the exclude expired.
  Both dropped.
- **`hermes_cli/agent_import.py`**: upstream moved it out of core into the
  agent-importers plugin (`1342ab8553`). The fork's only change was one glyph
  (`⚷` to `◆` in "Secrets stripped"); dropped, not reapplied.
- **`request_elicitation_consent`** (`tools/approval_prompt.py`): upstream's new
  `remember_key` parameter and scoped `allow_permanent`/`allow_session` taken; the
  fork's `read_only_hint` parameter and `_manual_gate_scope` wrapper kept.
- **`VALID_HOOKS`** (`hermes_cli/plugins.py`): union of the fork's three kanban
  hooks and upstream's `post_compaction`.

### Resolved 2026-10-05 (kept for history)

- **`request_elicitation_consent`** (`tools/approval_prompt.py`): upstream's new
  version (`762f419fe8`, `0df1837e81`, `ef1faa4cf8`: decline at once via
  `_no_user_can_answer()`, `pre_approval_request`/`post_approval_response` hooks,
  the agent thread's panel callback) taken whole. Only the fork's
  `_manual_gate_scope` (T8 head-of-line barrier) wraps the prompt call on top.
  Nothing dropped: upstream's `on_human_input_*` hooks are observer-only and
  cannot feed the barrier's depth counter.

### Resolved 2026-10-03 (kept for history)

- **`skills.always` / `skills.always_load`**: superseded by upstream
  `skills.auto_load` (`1976869c01`). `hermes_cli/skills_always.py` deleted; a
  key-merge shim in `agent/skill_commands.py` `resolve_auto_load_skills` folds
  the old keys into `auto_load`.
- **Cron model-drift helpers** in `hermes_cli/config.py`: upstream deliberately
  removed that design in `0469740ab3`; the 2026-09-14 rolling patch had brought
  them back. Deleted.
- **npm overrides for browserslist, fast-uri, sanitize-html**: upstream's lock
  resolves at or above the fork's CVE floors. Dropped.
- **Duration-balanced test slicing in `tests.yml`**: superseded by upstream's
  `HERMES_TEST_SLICE`.

### Resolved 2026-09-11 (kept for history)

- **Sandbox MITM proxy entries (former #1-#3: `Connection: close` on responses,
  the same gap on the plain-HTTP path, `dev-sandbox.sh` `NODE_DIR`
  auto-detection)** — RESOLVED, fork adopted upstream's design rather than
  reconciling. Upstream retired its entire bwrap/slirp4netns/MITM-proxy
  sandbox (`ea4cd375f8`, 2026-08-11) for a plain `GIT_CONFIG_GLOBAL`
  insteadOf redirect with no TLS interception at all. The fork's three
  patches in this area (`e1f76274ca` sandbox CA trust, `4ad7e3764e` PR #38
  Connection:close, `44302e6e31` PR #43 NODE_DIR auto-detection) were
  dropped from the rolling patch during the 2026-09-11 sync rebase — their
  target files no longer exist, and the bug classes they fixed (CA trust,
  connection reuse, unmounted host paths) have no surface left to recur on.
  `scripts/sandbox/proxy.py`, `stage2-run.sh`, `ssh-shim.sh`, `openssl.cnf`,
  and the old `dev-sandbox.sh` NODE_DIR logic are gone from the fork; E2E
  now runs upstream's own `docker/stage2-hook.sh` /
  `tests/install/installer-script-e2e.sh` design, unmodified. Verified via a
  full dead-reference sweep (code, CI YAML, scripts, fork-local tests) —
  nothing still points at the deleted paths.
- **`uv.lock` drift under `--locked` (former #4)** — RESOLVED, drop the
  fork's fix. Upstream fixed the exact symptom (`c9fa2bba45`, `6da0ae1cf5`)
  and it's already in `origin/main`'s `scripts/install.sh` (the `unset
  UV_NO_CONFIG UV_CONFIG_FILE` guard ahead of the locked sync). The fork's
  own `fix/e2e-reinstall-uv-lock-drift` branch was never merged and is
  superseded.

---

## Standing exception (not a watchlist entry, a permanent policy)

`plugins/self-check-enforcer` and `plugins/quality-gate` are **mandatory on
this fork** (`fork: make self-check-enforcer and quality-gate mandatory,
not opt-in`, #31) — loaded on every clone, not disable-able via the normal
plugin config/CLI path. If upstream ever ships an equivalent feature as
**opt-in**, do **not** "match upstream" by reverting the fork to opt-in.
The whole point of this fork's enforcement layer is that it runs without
being remembered/turned on by hand — see the fork README's "What you get"
section. Only adopt upstream's version if it is *also* mandatory-by-default,
or if it's straightforward to layer the fork's mandatory-loading behavior
on top of upstream's implementation.
