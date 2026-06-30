"""sandbox.run() isolation + result reporting (SPEC §6). Offline; spawns a real
pytest subprocess on synthetic code, so it is slower than the pure tests."""
from __future__ import annotations

import pytest

from tools.model_bakeoff import sandbox


def _oracle(tmp_path, body: str) -> str:
    p = tmp_path / "oracle_test.py"
    p.write_text(body)
    return str(p)


PASS_ORACLE = "from solution import f\n\ndef test_f():\n    assert f(2) == 3\n"


def test_correct_solution_passes(tmp_path):
    r = sandbox.run("def f(x):\n    return x + 1\n", _oracle(tmp_path, PASS_ORACLE), timeout_s=30)
    assert r.returncode == 0
    assert not r.timed_out


def test_wrong_solution_fails_but_does_not_time_out(tmp_path):
    r = sandbox.run("def f(x):\n    return x + 99\n", _oracle(tmp_path, PASS_ORACLE), timeout_s=30)
    assert r.returncode != 0
    assert not r.timed_out


def test_syntax_error_is_nonzero_exit(tmp_path):
    r = sandbox.run("def f(x)\n    return x\n", _oracle(tmp_path, PASS_ORACLE), timeout_s=30)
    assert r.returncode != 0


def test_infinite_loop_times_out_and_is_killed(tmp_path):
    oracle = _oracle(tmp_path, "from solution import go\n\ndef test_go():\n    go()\n")
    r = sandbox.run("def go():\n    while True:\n        pass\n", oracle, timeout_s=3)
    assert r.timed_out
    assert r.duration_s < 10  # killed promptly, not left running


def test_secret_env_vars_are_scrubbed(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_ZEN_API_KEY", "sk-must-not-leak-into-sandbox")
    oracle = _oracle(
        tmp_path,
        "import os\nfrom solution import f\n\n"
        "def test_no_secret():\n"
        "    assert os.environ.get('OPENCODE_ZEN_API_KEY') is None\n"
        "    assert f() == 1\n",
    )
    r = sandbox.run("def f():\n    return 1\n", oracle, timeout_s=30)
    assert r.returncode == 0  # the in-sandbox assertion that the secret is absent held


def test_read_capped_limits_bytes_and_flags_truncation(tmp_path):
    big = tmp_path / "big.txt"
    big.write_bytes(b"A" * (2 * sandbox.MAX_OUTPUT_BYTES))
    text, truncated = sandbox._read_capped(str(big))
    assert truncated
    assert len(text.encode()) <= sandbox.MAX_OUTPUT_BYTES
    small = tmp_path / "small.txt"
    small.write_bytes(b"hi")
    text2, trunc2 = sandbox._read_capped(str(small))
    assert text2 == "hi" and not trunc2


def test_oversized_file_write_is_blocked_by_fsize_limit(tmp_path):
    if sandbox.resource is None:  # pragma: no cover - non-POSIX
        pytest.skip("output cap relies on RLIMIT_FSIZE (POSIX only)")
    # The oracle asserts a > 1 MB write raises (RLIMIT_FSIZE blocks it via EFBIG). On kernels
    # that deliver SIGXFSZ as a kill instead, the run is flagged truncated. Either way the
    # write does not silently succeed and the run does not hang.
    oracle = _oracle(
        tmp_path,
        "import pytest\nfrom solution import go\n\n"
        "def test_blocked():\n"
        "    with pytest.raises(OSError):\n"
        "        go()\n",
    )
    code = ("def go():\n"
            "    with open('big.bin', 'wb') as fh:\n"
            "        fh.write(b'A' * (4 * 1024 * 1024))\n")
    r = sandbox.run(code, oracle, timeout_s=30)
    assert not r.timed_out
    assert r.returncode == 0 or r.truncated


def test_memory_bomb_is_contained_not_host_oom(tmp_path):
    if sandbox.resource is None:  # pragma: no cover - non-POSIX
        pytest.skip("heap cap relies on RLIMIT_AS (POSIX only)")
    oracle = _oracle(tmp_path, "from solution import go\n\ndef test_go():\n    go()\n")
    # RLIMIT_AS (1 GB) makes a 2 GB allocation fail inside the child instead of OOM-ing the host.
    code = "def go():\n    return len(bytearray(2 * 1024 * 1024 * 1024))\n"
    r = sandbox.run(code, oracle, timeout_s=30)
    assert not r.timed_out
    assert r.returncode != 0  # the allocation failed in-sandbox; the runner survived
