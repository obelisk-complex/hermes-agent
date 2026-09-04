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

### 1. Sandbox MITM proxy: `Connection: close` not enforced on responses

- **Fork fix:** `fix(sandbox): force Connection: close on proxied responses, not just requests` (PR #38, `scripts/sandbox/proxy.py`)
- **Symptom:** `scripts/sandbox/proxy.py`'s `handle_connect` serves one request per CONNECT tunnel then tears it down; it forced `Connection: close` on the outbound request but never rewrote the *response* header, so a keep-alive upstream (e.g. registry.npmjs.org behind a CDN) could tell npm's `https.Agent` the tunnel was reusable, racing into `SSLEOFError` under real `npm install` concurrency. Caused every scheduled `main` "Install & Update E2E" installer-route run to fail from 2026-08-28 onward.
- **Fork-only concern:** `scripts/sandbox/` is entirely fork-local dev/CI tooling (the E2E test sandbox), not something upstream carries at all — this entry exists in case upstream adds its own equivalent sandbox tooling and independently solves the same class of bug differently.
- **Upstream check:** does upstream have a `scripts/sandbox/proxy.py` equivalent at all yet? If not, nothing to reconcile.

### 2. Sandbox MITM proxy: same one-shot shape on the plain-HTTP path (investigated, not fixed)

`forward_http` (same file as #1) has the identical one-shot-per-connection
shape, but investigation found no live client currently routes plain HTTP
through this proxy — every real path (install one-liner, npm registry,
PyPI, git-via-SSH-shim) is HTTPS-only, so it never reaches this function.
Left as-is deliberately, not an oversight. **Re-check if this proxy ever
gains a plain-HTTP consumer** (e.g. an apt-mirror-style test) — the fix
would reuse the same `_read_headers`/`_force_connection_close` helpers #1
already added.

### 3. `scripts/dev-sandbox.sh`: `NODE_DIR` auto-detection trusts an unmounted host path

- **Fork fix:** see PR opened by the `nodedir-detection-fix` follow-up (branch `fix/sandbox-nodedir-detection`).
- **Symptom:** `command -v node` can resolve outside anything bind-mounted into the bwrap sandbox (e.g. an nvm-managed Node under `$HOME`), so `npm_config_nodedir` points node-gyp at headers the sandbox can't see, breaking native-addon builds (`node-pty` observed failing this way) regardless of the proxy fix above.
- **Fork-only concern:** fork-local test tooling, same as #1/#2.

### 4. E2E reinstall step: `uv.lock` drift under `--locked`

- **Fork fix:** see PR opened by the `uvlock-drift-fix` follow-up (branch `fix/e2e-reinstall-uv-lock-drift`).
- **Symptom:** during the installer-route reinstall, `uv sync --locked` fails with "lockfile needs to be updated" and silently falls back to an unpinned PyPI resolve — masks the failure instead of fixing it, defeats the point of a committed lockfile.
- **Upstream check:** `uv.lock`/`pyproject.toml` are shared with upstream (not fork-only) — if this was already stale on `NousResearch/hermes-agent` too, the fix (`uv lock` regeneration) will likely already be subsumed by the next upstream rebase; check whether the fork's regenerated lock still differs from upstream's after the next sync, and if not, drop this as a distinct fork commit.

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
