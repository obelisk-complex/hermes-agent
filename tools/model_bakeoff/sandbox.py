"""Run untrusted model code against a hidden oracle in an isolated subprocess
(SPEC §6). Best-effort isolation (documented as NOT a hard boundary on a rootless
WSL box): a fresh temp dir for the solution, a scrubbed env with no secret vars,
a hard wall-clock timeout, a process-group kill so runaway children die too, a
per-stream 1 MB output cap (RLIMIT_FSIZE), and a best-effort child heap cap
(RLIMIT_AS). Residual and NOT closed: a child can still fill the disk with many
sub-cap files, and code that ignores SIGXFSZ keeps running with a capped stdout.

The oracle (a pytest file) lives OUTSIDE the model-writable workdir and imports the
solution as the module `solution`, put on PYTHONPATH. The sandbox does not judge;
it returns the raw result for the scorer to interpret.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

try:
    import resource  # POSIX-only; drives the output and heap caps below.
except ImportError:  # pragma: no cover - non-POSIX
    resource = None

from .models import SandboxResult

# Per-stream output cap and best-effort child heap cap (SPEC §6). RLIMIT_FSIZE makes a
# runaway writer hit SIGXFSZ at MAX_OUTPUT_BYTES; RLIMIT_AS stops untrusted code OOM-ing the
# host. Both are POSIX-only and best-effort: they do NOT bound total disk (a child can write
# many sub-cap files) nor survive code that ignores SIGXFSZ. 1 GB AS is verified to pass a
# normal pytest run on this box; the corpus is pure-python (no heavy imports needing more).
MAX_OUTPUT_BYTES = 1024 * 1024
MAX_ADDRESS_SPACE_BYTES = 1024 * 1024 * 1024

# Minimal env allowlist: enough to run Python, nothing that could carry a secret.
_ENV_ALLOW = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT")


def _scrubbed_env(workdir: str) -> dict[str, str]:
    env = {k: os.environ[k] for k in _ENV_ALLOW if k in os.environ}
    env["PYTHONPATH"] = workdir            # so the oracle can `import solution`
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["HOME"] = workdir                  # contain any stray writes
    env["PYTHONHASHSEED"] = "0"
    return env


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        if hasattr(os, "killpg"):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:  # pragma: no cover - non-POSIX
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass


def _apply_rlimits() -> None:  # pragma: no cover - runs in the forked child
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT_BYTES, MAX_OUTPUT_BYTES))
    try:
        resource.setrlimit(resource.RLIMIT_AS, (MAX_ADDRESS_SPACE_BYTES, MAX_ADDRESS_SPACE_BYTES))
    except (ValueError, OSError):  # some kernels reject an AS limit; the FSIZE cap still holds.
        pass


def _read_capped(path: str) -> tuple[str, bool]:
    """Read at most MAX_OUTPUT_BYTES from a captured stream; flag if it hit the cap."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            raw = f.read(MAX_OUTPUT_BYTES)
    except OSError:
        return "", False
    return raw.decode("utf-8", errors="replace"), size >= MAX_OUTPUT_BYTES


def run(
    code: str,
    oracle_path: str,
    timeout_s: int = 60,
    solution_module: str = "solution",
    python: str | None = None,
) -> SandboxResult:
    python = python or sys.executable
    workdir = tempfile.mkdtemp(prefix="bakeoff_sol_")
    try:
        with open(os.path.join(workdir, f"{solution_module}.py"), "w", encoding="utf-8") as f:
            f.write(code)

        cmd = [
            python, "-m", "pytest", oracle_path,
            "-q", "-p", "no:cacheprovider",
            "--import-mode=importlib",
            "--ignore-glob=*solution*",
            "-o", "addopts=",
        ]
        stdout_path = os.path.join(workdir, "_stdout.txt")
        stderr_path = os.path.join(workdir, "_stderr.txt")
        start = time.monotonic()
        popen_kwargs: dict = dict(cwd=workdir, env=_scrubbed_env(workdir))
        if hasattr(os, "setsid"):
            popen_kwargs["start_new_session"] = True  # own process group for killpg
        if resource is not None and hasattr(os, "fork"):
            popen_kwargs["preexec_fn"] = _apply_rlimits  # cap output + heap in the child

        # Output goes to files (not pipes) so a runaway writer cannot deadlock the parent or
        # balloon its memory; RLIMIT_FSIZE caps each file and we read back at most the cap.
        timed_out = False
        with open(stdout_path, "wb") as out_f, open(stderr_path, "wb") as err_f:
            proc = subprocess.Popen(cmd, stdout=out_f, stderr=err_f, **popen_kwargs)
            try:
                proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                _kill_group(proc)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                timed_out = True

        out, out_trunc = _read_capped(stdout_path)
        err, err_trunc = _read_capped(stderr_path)
        sigxfsz = getattr(signal, "SIGXFSZ", None)
        killed_by_cap = sigxfsz is not None and proc.returncode == -sigxfsz
        rc = proc.returncode if proc.returncode is not None else -signal.SIGKILL
        return SandboxResult(
            returncode=(-signal.SIGKILL if timed_out else rc),
            stdout=out, stderr=err,
            timed_out=timed_out,
            truncated=(out_trunc or err_trunc or killed_by_cap),
            duration_s=time.monotonic() - start,
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
