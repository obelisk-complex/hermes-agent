# Upstream sync runbook

How the nightly `Sync Upstream` workflow keeps this fork current with
`NousResearch/hermes-agent`, how to read a failure, and how to fix one so it
never recurs. The trunk is the **remote** `origin/main`; every `hermes update`
consumer fast-forwards to it (falling back to a hard reset only if it has
diverged), so a broken sync must never reach it.

## How the sync works

`.github/workflows/sync-upstream.yml` runs on manual dispatch only (the
nightly 11:00 UTC cron was dropped 2026-09-14 — `gh workflow run
sync-upstream.yml --repo obelisk-complex/hermes-agent`). Each run:

1. Checks out `origin/main`, adds `upstream`, fetches `upstream/main`.
2. Seeds `git rerere` from the committed `ci/rerere-cache/` (each `<hash>/`
   holds the `preimage` + `postimage` of one resolved conflict).
3. Merges `upstream/main` into fork `main` (single-shot — merge pauses at
   most once, never per-commit the way rebase did). Recorded rerere
   resolutions auto-stage; if any path is still unmerged after that, it's
   a genuinely new conflict and the run aborts for a human. A whole-tree
   conflict-marker scan runs immediately after (catches a marker outside
   the four `py_compile`'d files — `uv.lock`, `.ts`, `.json`, `.md`, etc),
   and every file the merge touched is written to the run's job summary
   for review, even on a green run.
4. **Pre-push gate:** `py_compile`s the import-critical files, runs every
   fork-local test file (found by diffing the merged tree's `tests/` against
   `upstream/main`'s — not a hand-maintained list), and checks `uv lock
   --check` + `ruff check .`. The push happens only if all of this passes,
   so a broken merge never lands on `origin/main`.
5. **Plain (non-force) pushes** the validated, merged tree to `origin/main`,
   authored by `GITHUB_TOKEN` (the `SYNC_PAT` fine-grained PAT this used to
   run as is retired — dead 2026-08-30, revoked). Because the push is a
   plain fast-forward, a rejection here means something else moved `main`
   concurrently (a human push or another dispatch) — NOT a bad merge; see
   the "push rejected" shape below. If the merge touched anything under
   `.github/workflows/`, step 5 never happens: `GITHUB_TOKEN` cannot push
   those paths on any branch (a hard GitHub-side restriction, not a
   `permissions:` gap), so a guard between steps 4 and 5 fails loud instead
   and leaves `origin/main` untouched — see "Fixing it" below for the
   manual-push procedure that case needs.
6. **Post-push CI watch (advisory):** `GITHUB_TOKEN`-authored pushes
   deliberately do not trigger downstream workflow runs (recursion guard),
   so this step explicitly dispatches `ci.yaml` on `main` (`gh workflow run
   ci.yaml --ref main`) right after the push, then watches that run's `All
   required checks pass` job (up to 45 min) and — if it doesn't go green —
   reports it through the tracking issue, but the **sync itself stays
   green**. The sync's job is to merge the fork's
   customisation onto upstream latest and push it; it does NOT gate on the
   health of upstream's suite (a red upstream or a runner-label problem —
   e.g. the 2026-08-22 larger-runner streak, which a personal account
   cannot provision — must never block the daily patch application). What
   the watch guarantees is that a red `main` is *announced* within minutes
   instead of sitting unnoticed.

There are now **three distinct failure/notice shapes**, and the tracking
issue text tells you which one you're looking at:

- **Merge or pre-push gate failed → `origin/main` was NOT updated, sync
  RED.** A conflict with no recorded resolution, or a pre-push check
  failure, aborts the job before the push. This is the failure most of this
  doc is about. A merge that touches `.github/workflows/` is one specific
  case of this: `GITHUB_TOKEN` cannot push those paths, so the "Check for
  workflow-file changes" step aborts before even reaching the pre-push gate
  — see "Manual push for workflow-file changes" below, not the rerere
  procedure.
- **Push rejected → `origin/main` was NOT updated, sync RED, but this is
  NOT a merge conflict.** The merge and pre-push gate both PASSED, but the
  plain push to `origin/main` was rejected because something else moved
  `main` after the run started (a concurrent human push or another
  dispatch). Do not run the rerere resolve procedure — just re-dispatch the
  workflow.
- **Post-push CI watch reported red → `origin/main` WAS updated, sync still
  GREEN.** The merge and pre-push gate both passed, the push happened, and
  the full `ci.yaml` run on that pushed SHA didn't come back green (or never
  appeared, or hung past 45 min — the issue body names which). `origin/main`
  is live and every `hermes update` consumer will fast-forward to it. The
  sync is fine; decide whether the red main needs action. **If the red is a
  real break, roll back first, investigate second:**
  `git push --force origin <pre-sync-sha>:main` using the SHA embedded
  directly in the tracking issue body (also printed inside the "Push merged
  main" step's own plain log output). Then diagnose the failing `ci.yaml`
  run linked in the issue like any other CI failure — it is not a merge
  problem, so the "Fixing it" section below (rerere, `ci/rerere-cache/`)
  does not apply.

All three shapes open or update the **same** single issue labelled
`sync-upstream-blocked` — the Actions tab alone went unwatched for a 12-day
failure streak (2026-07-22 to 2026-08-02) before anyone noticed, so the
workflow now self-announces. The red-CI notice auto-closes on the next sync
whose watch confirms a green CI gate; the blocked-sync notice auto-closes on
the next successful sync. The workflow does not attempt to resolve the
merge-conflict shape itself (see the "Fixing it" section below — this is
still a one-time human step, deliberately: auto-resolving a semantic
conflict is how the fork previously lost real hunks silently). The
push-rejected shape needs no resolution at all, just a re-dispatch.

## Reading a failure

Open the failed run (linked from the `sync-upstream-blocked` issue, or via
`gh run list --repo obelisk-complex/hermes-agent --workflow=sync-upstream.yml`).
There is **no per-path NEW/STALE job-summary diagnosis today** — a prior
version of this doc described one; nothing in `sync-upstream.yml` writes it.
What's actually there to read:

- **"Merge upstream into fork main"** step log: git's own merge output
  names the conflicting path(s) directly. A conflict with no recorded
  resolution ends in `::error::Upstream merge hit an UNKNOWN conflict (no
  recorded rerere resolution)...` (generic — the path is in the git output
  just above it, not in this line).
- **"Validate merged tree (pre-push gate)"** step log: a `py_compile` failure
  names the exact file with conflict markers left in it.
- **"Find fork-local test files"** / **"Run fork-local tests against the
  merged tree"** step logs: a fork-owned test broke under an upstream
  refactor the merge replayed cleanly (git sees no conflict — the breakage
  is semantic, not textual). The pytest output names the failing test.
- **"Verify uv.lock against the merged tree (pre-push gate)"** / **"ruff
  check the merged tree (pre-push gate)"** step logs: the merge replayed a
  lock pin or introduced a lint violation that upstream's own
  `pyproject.toml`/style changes now disagree with.
- Either failure opens/updates the `sync-upstream-blocked` issue (see above),
  but today that issue links to the run rather than embedding the path — you
  still have to open the log.

Whether git classifies a conflict as brand-new vs a previously-recorded
resolution that no longer applies cleanly (a real distinction — see `git
rerere status` / `MERGE_RR`) is not surfaced anywhere the workflow writes to.
If this keeps costing real triage time, teaching the merge step to capture
`git diff --name-only --diff-filter=U` and put it in the `::error::` line and
the issue body is a contained follow-up — flagging it, not doing it here.

## Fixing it (reproduce, resolve, PROVE, seed, dispatch)

1. **Reproduce in an isolated clone.** Use a fresh `git clone` of the fork URL,
   not a worktree: linked worktrees share `.git/rr-cache` with the parent, which
   would invalidate the proof.
   ```sh
   git clone https://github.com/obelisk-complex/hermes-agent.git /tmp/sync-proof  # no-tmp: ok — manual clone in a human's own terminal, not model-executed
   cd /tmp/sync-proof  # no-tmp: ok — same manual clone
   git remote add upstream https://github.com/NousResearch/hermes-agent.git
   git fetch upstream main
   mkdir -p .git/rr-cache && cp -R ci/rerere-cache/. .git/rr-cache/
   git config rerere.enabled true && git config rerere.autoupdate true
   git config user.email "265670482+obelisk-complex@users.noreply.github.com"
   git config user.name "obelisk-complex"
   git merge upstream/main      # stops at the unresolved conflict
   ```
2. **Resolve once, as a union where the change is additive.** When upstream and
   the fork inserted independent lines at the same spot, keep BOTH (upstream's
   block first, to match upstream's hunk order). Then `git add <file>` and run
   `git commit --no-edit` once all conflicts are resolved and staged. With
   `rerere.autoupdate`, the resolution is recorded automatically.
3. **Capture the new entry.** The new `rr-cache/<hash>/` now has a `postimage`.
   Copy `preimage` + `postimage` into `ci/rerere-cache/<hash>/`.
4. **(Optional, but builds confidence before landing a new cache entry.)**
   Prove the resolution replays from a clean clone seeded only from the
   committed `ci/rerere-cache`. Under the old rebase model this was
   mandatory — a resolution had to replay identically forever. Under
   merge, a resolution is committed once as part of the merge commit and
   never needs replaying again, so this step is now a confidence check,
   not a required gate. Still worth doing when you're not sure the
   resolution generalizes.
5. **Run the pre-push gate locally** on the merged tree:
   ```sh
   export PYTHONPATH="$PWD"
   python3 -m py_compile hermes_cli/main.py hermes_cli/plugins.py \
     hermes_cli/kanban_db.py agent/conversation_loop.py tools/delegate_tool.py

   # Fork-local test files: anything under tests/ that exists in the
   # merged tree but not in upstream/main — upstream will never fix these
   # for you, so they need to actually run against the merged tree.
   FORK_TESTS=$(git diff --name-only --diff-filter=d upstream/main HEAD -- tests \
     | grep -E '\.py$' | paste -sd:)
   scripts/run_tests.sh --files "$FORK_TESTS"

   uv lock --check
   ruff check .
   ```
6. **Commit the seed and sync.** Commit `ci/rerere-cache/<hash>/` to `origin/main`
   (an additive, fork-only change), then dispatch the workflow and watch it go
   green:
   ```sh
   gh workflow run sync-upstream.yml --repo obelisk-complex/hermes-agent
   ```
   Record the pre-sync `origin/main` SHA first; if a run ever pushes a bad
   tree, roll back with `git push --force origin <pre-sync-sha>:main`.

## Manual push for workflow-file changes

`GITHUB_TOKEN` cannot push commits touching `.github/workflows/`, on any
branch — a hard GitHub-side restriction, not something a `permissions:`
grant can lift. Since the `SYNC_PAT` fine-grained PAT that used to cover
this is retired, a merge pulling in an upstream workflow-file change stops
at the "Check for workflow-file changes" step with the changed paths in the
log, `origin/main` untouched. To land it:

```sh
git clone https://github.com/obelisk-complex/hermes-agent.git /tmp/sync-workflow-push  # no-tmp: ok — manual clone in a human's own terminal, not model-executed
cd /tmp/sync-workflow-push  # no-tmp: ok — same manual clone
git remote add upstream https://github.com/NousResearch/hermes-agent.git
git fetch upstream main
git checkout -b sync/workflow-files
git merge upstream/main   # same merge the workflow attempted; resolve any conflict as in step 2 above
```

**Run the pre-push gate locally before opening the PR** — see "Fixing it"
step 5 above for the exact commands (`py_compile`, fork-local tests, `uv
lock --check`, `ruff check .`). This PR bypasses every automated check the
workflow itself applies, and under the merge model whatever lands here is
permanent history.

```sh
git push -u origin sync/workflow-files   # your own push credentials — not GITHUB_TOKEN
```

Open a PR from `sync/workflow-files` and merge it normally (a human merging
through the web UI is unaffected by the `GITHUB_TOKEN` restriction — it only
applies to token-authored `git push`).

**When merging this PR through the GitHub web UI, use "Create a merge
commit" — never "Squash and merge" or "Rebase and merge".** Either of
those would rewrite the merge history this whole sync model exists to
keep permanent. Confirm the repo's branch settings actually expose the
merge-commit option before relying on this.

Re-dispatch `sync-upstream.yml`
afterwards; with the workflow files already current, that merge step is a
no-op and the rest of the sync proceeds as usual.

## Durability: commit an unpushed runtime resolution within 7 days

A resolution recorded during a run that successfully **pushed** is
permanent the moment it lands — it's part of the merge commit on
`origin/main`, never re-derived, never at risk of being lost. The
remaining risk is narrower than it was under rebase: a run that
resolves a conflict at runtime but then **aborts before pushing** (the
workflow-file guard, `py_compile`, fork-local tests, `uv lock --check`,
or `ruff` can all still fail after the merge and before the push) only
has that resolution in the `actions/cache` entry keyed
`rerere-cache-${{ github.run_id }}`, which the Actions cache backend
evicts after 7 days of no matching restore. Treat any run that resolved
conflicts but did not push as a prompt to check whether `.git/rr-cache`
grew an entry not yet in `ci/rerere-cache/`, and commit it (see "Fixing
it" above) before that window closes.

## Notes

- **`MERGE_RR` location.** In CI (a plain checkout) it is `.git/MERGE_RR`. In a
  linked worktree it is `.git/worktrees/<name>/MERGE_RR` (use
  `git rev-parse --git-path MERGE_RR`).
- **Long-term simplification.** There is no periodic squashing of the fork's
  customisation under the merge model — the whole point of moving off rebase
  is that a conflict resolved once becomes permanent history, and squashing
  across merge commits would require rewriting that history. The one
  sanctioned exception is a full, deliberate, ANNOUNCED re-baseline (this
  fork did one on 2026-08-03), which resets the permanence property on
  purpose and needs a backup ref plus a re-seeded rerere cache. It may be
  needed again if upstream absorbs most of the fork's remaining
  customisation, but it's a deliberate event, not a routine practice.
