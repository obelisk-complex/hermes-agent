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

- **electron 41.10.3** (fork CVE fix, GHSA-9f4c-93c8-jc8g / GHSA-r4w5-6pfg-jxp5).
  Upstream rolled back to 40.10.2 in `bb8280b753` over a Windows VC++/extract-zip
  fresh-install problem and calls 41.x deferred. Keep ours until upstream moves
  to 41+; untested with upstream's electron-builder 27.0.0-alpha.6. Re-checked
  2026-10-05: upstream `apps/desktop/package.json` is still on 40.10.2.
- **`@xmldom/xmldom` overrides** (0.8.15 / 0.9.12, GHSA-6gmq-8vp8-gcm6). Upstream
  still resolves 0.8.13 (deprecated) and 0.9.10 in `tests-js` (re-checked
  2026-10-05). Drop once upstream's lock is at or above.
- **MCP readOnlyHint lookup** (`tools/mcp_tool.py`, keyed via
  `_resolve_server_key` since 2026-10-03). Its only caller,
  `tools/mcp_tool_sampling.py`, runs in the MCP receive loop, which does not
  inherit context variables, so in a multiplexed gateway the scope may still
  resolve to none. Unverified. Upstream's elicitation path is unchanged
  (`_pending_call_context` replay only wraps the consent call, not the hint
  lookup), so this stays open.
- **`install_modify_other_keys_aliases()` also installs the keypress data
  normalisation** (`hermes_cli/pt_input_extras.py`). Upstream calls
  `install_keypress_data_normalization()` only from `cli.py`; the curses pickers
  (`hermes_cli/curses_ui.py`) install the aliases alone, so they miss the
  normalisation the fork adds. Drop once upstream's `install_modify_other_keys_aliases()` (or
  `curses_ui.py`) installs the normalisation itself.
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
  `ENFORCEMENT` to `"advisory"` on the fork, or clean the 40.

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
