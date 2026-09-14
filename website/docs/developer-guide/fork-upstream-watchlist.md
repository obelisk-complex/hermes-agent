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

_None open as of 2026-09-11._

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
