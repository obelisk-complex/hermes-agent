"""Integration coverage for profile-local MCP discovery in slash workers."""

from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import textwrap
import threading

import pytest
import yaml

pytest.importorskip("mcp.server.fastmcp")

# Budget for the worker's FIRST reply. This is not a warm round-trip: before it
# can emit a byte the worker cold-boots a fresh interpreter, imports the whole
# ``cli`` stack, spawns a SECOND interpreter for the FastMCP probe server and
# completes the stdio handshake, builds a HermesCLI, then renders /tools.
# Measured cost of that sequence: ~1.3s on an idle 16-core box, ~5.2s at 6x CPU
# oversubscription, and ~9-10s on a CI runner already running 8 test workers --
# which is why a 10s budget sat exactly on the cliff edge and tipped over
# whenever the runner was busy (the reply simply had not been written yet).
#
# ``Queue.get`` returns the instant the reply lands, so a generous ceiling costs
# nothing when the worker is healthy; it only bounds how long a genuinely broken
# worker takes to be reported. Sizing it for the slowest plausible CI runner
# rather than the fastest developer laptop is therefore free.
RESPONSE_TIMEOUT_S = float(os.environ.get("HERMES_TEST_SLASH_WORKER_TIMEOUT_S", "60"))

# Bound the worker puts on MCP discovery before it snapshots the tool list.
#
# This is the timeout that actually decides this test, and it is NOT the one
# above. The worker calls ``wait_for_mcp_discovery()``, which joins the
# discovery thread for at most ``mcp_discovery_timeout`` (config.yaml; default
# 1.5s) and then builds a HermesCLI regardless. Discovery has to spawn a second
# interpreter, import FastMCP and finish a stdio handshake, so on a loaded CI
# runner it does not land inside 1.5s -- the join gives up, the tool snapshot is
# taken without the MCP tools, and the worker answers /tools promptly and
# cheerfully with only the builtins. Nothing times out and nothing errors: the
# assertion below just fails to find the tool. Raising RESPONSE_TIMEOUT_S cannot
# help, because the reply was never late.
#
# ``Thread.join`` returns the instant discovery completes, so a large bound
# costs a healthy worker nothing; it converts a race against ambient load into a
# wait. Sized like RESPONSE_TIMEOUT_S, for the same reason.
DISCOVERY_TIMEOUT_S = RESPONSE_TIMEOUT_S


def test_profile_local_mcp_tool_is_visible_in_slash_worker(tmp_path):
    profile_home = tmp_path / "profile-home"
    profile_home.mkdir()
    marker = "profile-local-61922"
    server = tmp_path / "fastmcp_probe.py"
    server.write_text(
        textwrap.dedent(
            f"""
            from mcp.server.fastmcp import FastMCP

            mcp = FastMCP("profileprobe")

            @mcp.tool()
            def hermes_61922_profile_probe() -> str:
                return {marker!r}

            if __name__ == "__main__":
                mcp.run(transport="stdio")
            """
        ),
        encoding="utf-8",
    )
    (profile_home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "mcp_discovery_timeout": DISCOVERY_TIMEOUT_S,
                "mcp_servers": {
                    "profileprobe": {
                        "enabled": True,
                        "command": sys.executable,
                        "args": [str(server)],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    for key in list(env):
        if key.endswith("_API_KEY") or key.endswith("_TOKEN"):
            env.pop(key)
    env["HERMES_HOME"] = str(profile_home)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    env["HERMES_SLASH_WATCHDOG_GRACE_S"] = "0"
    env["HERMES_SLASH_WATCHDOG_POLL_S"] = "0.05"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-m",
            "tui_gateway.slash_worker",
            "--session-key",
            "agent:main:tui:dm:mcp-profile-test",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=tmp_path,
    )
    output: queue.Queue[str] = queue.Queue()
    try:
        assert proc.stdin is not None
        assert proc.stdout is not None
        assert proc.stderr is not None
        stdout = proc.stdout
        threading.Thread(
            target=lambda: output.put(stdout.readline()),
            daemon=True,
        ).start()

        # Drain stderr continuously. Leaving a PIPE unread is a deadlock waiting
        # to happen: once the worker writes more than the pipe buffer (64 KiB on
        # Linux) it blocks in write() forever and we would sit here until the
        # timeout with no idea why -- the same symptom as a slow boot, but
        # unfixable by waiting. Draining also means a failing worker's traceback
        # actually reaches the report instead of dying with the pipe.
        stderr_lines: list[str] = []
        stderr_pipe = proc.stderr
        threading.Thread(
            target=lambda: stderr_lines.extend(stderr_pipe),
            daemon=True,
        ).start()

        def _worker_stderr() -> str:
            captured = "".join(stderr_lines).strip()
            return f"\n--- worker stderr ---\n{captured}" if captured else ""

        proc.stdin.write(json.dumps({"id": 1, "command": "/tools"}) + "\n")
        proc.stdin.flush()
        try:
            line = output.get(timeout=RESPONSE_TIMEOUT_S)
        except queue.Empty:
            pytest.fail(
                f"slash worker produced no /tools response within "
                f"{RESPONSE_TIMEOUT_S:g}s (still running: {proc.poll() is None})"
                f"{_worker_stderr()}"
            )
        # An empty line means readline() hit EOF: the worker exited rather than
        # replying. Say so, instead of letting json.loads("") raise an opaque
        # JSONDecodeError that hides the real cause.
        if not line:
            pytest.fail(
                f"slash worker exited before replying to /tools "
                f"(returncode={proc.poll()}){_worker_stderr()}"
            )
        response = json.loads(line)
        assert response["ok"] is True, f"worker returned an error: {response}"
        assert "mcp__profileprobe__hermes_61922_profile_probe" in response["output"]
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
