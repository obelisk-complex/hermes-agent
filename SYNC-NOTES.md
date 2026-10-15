# Sync notes — 2026-10-15

Completes the 2026-10-12 sync (session killed mid-verification) and extends it
to upstream tip `3bdb82d6ec`. Branch: `sync/upstream-2026-10-12` (worktree
`.worktrees/sync-2026-10-12`), now a single merge chain on top of pre-sync
`origin/main` (`809bf295d3`).

## Range

- Pre-sync `origin/main`: `809bf295d3` (2026-10-05 sync via PR #52)
- Upstream merged: `63d0b94120` (Oct 12, 2909 commits, worktree's original
  merge) then `upstream/main` tip `3bdb82d6ec` (further 2813 commits, this
  session)
- Pre-merge fork parent: `ee114cb379` (Oct 12 rerere seed) + `0edcbf19e7`
  (this session's seam repair)

## Conflicts resolved (3bdb82d6ec merge)

- 16 workflow files + `.github/actionlint.yaml`: upstream's blacksmith runner
  migration vs the fork's standing standard-runner policy (larger-runner
  labels never resolve on a personal account). Fork side kept on all hunks;
  `actionlint.yaml` deleted — upstream removed it and no fork workflow
  references blacksmith labels.
- `package.json`: union (fork's @xmldom CVE pins + upstream's new overrides).
- `hermes_cli/config.py`: upstream's decomposition adopted (config_show.py
  sibling carries the section helpers), then fork's
  `warn_auto_approve_dependencies` (T4, dual-signal plan) grafted back with
  its `set_config_value` call site.

## Merge-time regressions found and fixed

- `tools/approval.py` (from the Oct-12 merge): upstream's post-tirith
  `_human_decision` signature adopted while the fork's dual-signal
  `_smart_gate` still consumed `warnings` — NameError on every smart-mode
  approval. `warnings` parameter restored; tirith stays retired (upstream's
  config migration 49 -> 50 removed the scanner; detections were ported into
  core).
- `tests/test_b2_kanban_hooks.py`: `VALID_HOOKS` annotation regex updated for
  upstream's `Set[str]` -> `set[str]`.
- `tests/tools/test_dual_signal_command_gate.py`: probe changed to
  `rm -rf ~/.hermes/config` — the pm-managed interpreter now lives under
  `~/.hermes`, so the runtime self-delete floor (#58748) fires before the gate
  on the old probe.

## Verification (pre-push gate, all green on HEAD `ace61d3d55`)

- py_compile of the import-critical files: pass
- Fork-local tests (39 files, diff vs upstream tip): 474 passed, 0 failed
- `uv lock --check`: pass
- `ruff check .` (config-respecting): pass
- Tree-diff completeness: `git diff --name-only 809bf295d3..HEAD` minus
  `809bf295d3..upstream/main` = only fork-owned files (plugins/quality-gate,
  self-check-enforcer, dual-signal modules, fork tests/docs, rerere seeds).
  No fork-only extras, nothing from the upstream range dropped.

## Deliberate deferrals to upstream this sync

- tirith scanner: retired (upstream owns the decision; detections live in
  core).
- Content-level `security.scan` action tag: dormant in the taxonomy, kept for
  compat.
- Watchlist: `website/docs/developer-guide/fork-upstream-watchlist.md`
  refreshed.

## Post-sync state

- `ci/rerere-cache/` left intact (harmless historical preimages; this merge's
  resolutions are permanent merge history, nothing pending in rerere).
- Consumers fast-forward: `hermes update` picks up `origin/main`.