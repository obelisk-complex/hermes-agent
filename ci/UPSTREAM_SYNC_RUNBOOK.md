# Upstream sync runbook

How the nightly `Sync Upstream` workflow keeps this fork current with
`NousResearch/hermes-agent`, how to read a failure, and how to fix one so it
never recurs. The trunk is the **remote** `origin/main`; every `hermes update`
consumer hard-resets to it, so a broken sync must never reach it.

## How the sync works

`.github/workflows/sync-upstream.yml` runs daily (11:00 UTC) and on manual
dispatch. Each run:

1. Checks out `origin/main`, adds `upstream`, fetches `upstream/main`.
2. Seeds `git rerere` from the committed `ci/rerere-cache/` (each `<hash>/`
   holds the `preimage` + `postimage` of one resolved conflict).
3. Rebases the fork's custom commits onto `upstream/main`. Recorded resolutions
   auto-replay; the loop drives `git rebase --continue` through each
   auto-resolved step.
4. **Pre-push gate:** `py_compile`s the import-critical files and runs the guard
   tests. The force-push happens only if this passes, so a broken rebase never
   lands on `origin/main`.
5. Force-pushes the validated, rebased tree to `origin/main`.

A conflict with **no** recorded resolution makes the rebase abort, fail loud,
and leave `origin/main` untouched. That is the failure you are here to fix.

## Reading a failure

Open the failed run. The **job Summary** carries a per-path diagnosis written by
the workflow before it aborts:

- `NEW <path> (hash <h>)`: no recorded resolution for this conflict. Resolve it
  once and add `ci/rerere-cache/<h>/`.
- `STALE <path> (hash <h>)`: a recorded postimage exists but no longer applies
  cleanly (upstream changed lines near the resolved zone). Re-resolve and
  **renew** that committed postimage.

These two look identical without the diagnosis, which is why it exists. The
classification comes from git's own `MERGE_RR` map (one `hash<TAB>path` record
per active conflict); STALE vs NEW is decided purely by whether
`rr-cache/<hash>/postimage` exists. (`git rerere diff` is **not** a stale
signal: it is non-empty for brand-new conflicts too.)

## Fixing it (reproduce, resolve, PROVE, seed, dispatch)

1. **Reproduce in an isolated clone.** Use a fresh `git clone` of the fork URL,
   not a worktree: linked worktrees share `.git/rr-cache` with the parent, which
   would invalidate the proof.
   ```sh
   git clone https://github.com/obelisk-complex/hermes-agent.git /tmp/sync-proof
   cd /tmp/sync-proof
   git remote add upstream https://github.com/NousResearch/hermes-agent.git
   git fetch upstream main
   mkdir -p .git/rr-cache && cp -R ci/rerere-cache/. .git/rr-cache/
   git config rerere.enabled true && git config rerere.autoupdate true
   git config user.email "265670482+obelisk-complex@users.noreply.github.com"
   git config user.name "obelisk-complex"
   git rebase upstream/main      # stops at the unresolved conflict
   ```
2. **Resolve once, as a union where the change is additive.** When upstream and
   the fork inserted independent lines at the same spot, keep BOTH (upstream's
   block first, to match upstream's hunk order). Then `git add <file>` and drive
   `GIT_EDITOR=true git rebase --continue` to completion. With
   `rerere.autoupdate`, the resolution is recorded automatically.
3. **Capture the new entry.** The new `rr-cache/<hash>/` now has a `postimage`.
   Copy `preimage` + `postimage` into `ci/rerere-cache/<hash>/`.
4. **PROVE the replay from a clean clone** seeded ONLY from the committed
   `ci/rerere-cache` (now including the new entry). A second fresh clone must
   rebase onto `upstream/main` and complete with zero unmerged paths and zero
   manual edits. If it stops, the committed seed is insufficient: capture the
   missing resolution and repeat. This proof is mandatory: a preimage hash is
   context-free (always found) but a postimage is applied as a fuzzy patch, so a
   resolution can be found yet fail to apply.
5. **Run the pre-push gate locally** on the rebased tree:
   ```sh
   export PYTHONPATH="$PWD"
   python3 -m py_compile hermes_cli/main.py hermes_cli/plugins.py \
     hermes_cli/kanban_db.py agent/conversation_loop.py tools/delegate_tool.py
   python3 tests/agent/test_on_output_retry_loop.py
   python3 tests/hermes_cli/test_update_safety.py
   python3 tests/agent/test_hook_contract.py
   python3 tests/tools/test_delegate_instructions.py
   ```
6. **Commit the seed and sync.** Commit `ci/rerere-cache/<hash>/` to `origin/main`
   (an additive, fork-only change), then dispatch the workflow and watch it go
   green:
   ```sh
   gh workflow run sync-upstream.yml --repo obelisk-complex/hermes-agent
   ```
   Record the pre-sync `origin/main` SHA first; if a run ever force-pushes a bad
   tree, roll back with `git push --force origin <pre-sync-sha>:main`.

## Durability: commit every runtime resolution within 7 days

If a sync resolves a conflict at runtime that is not in the committed
`ci/rerere-cache`, the run emits `::warning::rerere recorded resolution(s) at
runtime ... commit these`. The Actions rr-cache has a 7-day TTL, so an
uncommitted runtime resolution is silently lost and the same conflict re-fails
later. Reproduce it (steps above), capture the entry, and commit it.

## Notes

- **`MERGE_RR` location.** In CI (a plain checkout) it is `.git/MERGE_RR`. In a
  linked worktree it is `.git/worktrees/<name>/MERGE_RR` (use
  `git rev-parse --git-path MERGE_RR`).
- **Long-term simplification.** Squashing the fork's customisation into a single
  rolling-patch commit reduces the rerere surface to at most one conflict-context
  per upstream change and removes the multi-step replay loop. It is the standard
  exit ramp for a long-lived fork if maintaining many per-conflict entries
  becomes the bottleneck.
