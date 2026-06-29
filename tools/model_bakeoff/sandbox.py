"""Run untrusted model code against a hidden oracle in an isolated subprocess
(SPEC §6). Best-effort isolation (documented as NOT a hard boundary on a rootless
WSL box): a fresh temp dir for the solution, a scrubbed env with no secret vars,
a hard wall-clock timeout, and a process-group kill so runaway children die too.

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

from .models import SandboxResult

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
        start = time.monotonic()
        popen_kwargs: dict = dict(
            cwd=workdir,
            env=_scrubbed_env(workdir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if hasattr(os, "setsid"):
            popen_kwargs["start_new_session"] = True  # own process group for killpg

        proc = subprocess.Popen(cmd, **popen_kwargs)
        try:
            out, err = proc.communicate(timeout=timeout_s)
            return SandboxResult(
                returncode=proc.returncode, stdout=out, stderr=err,
                duration_s=time.monotonic() - start,
            )
        except subprocess.TimeoutExpired:
            _kill_group(proc)
            try:
                out, err = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                out, err = "", ""
            return SandboxResult(
                returncode=-signal.SIGKILL, stdout=out or "", stderr=err or "",
                timed_out=True, duration_s=time.monotonic() - start,
            )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
