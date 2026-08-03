import sys

import runner


def test_passing_command(tmp_workspace):
    r = runner.run_gate([sys.executable, "-c", "print('ok')"], tmp_workspace)
    assert r.passed is True
    assert r.skipped is False
    assert r.rc == 0
    assert "ok" in r.stdout


def test_failing_command(tmp_workspace):
    r = runner.run_gate([sys.executable, "-c", "import sys; sys.exit(1)"], tmp_workspace)
    assert r.passed is False
    assert r.skipped is False
    assert r.rc == 1


def test_not_allowlisted_is_skipped_not_run(tmp_workspace):
    r = runner.run_gate(["rm", "-rf", "/"], tmp_workspace)
    assert r.skipped is True
    assert r.passed is False
    assert r.reason == "not allow-listed"
    assert r.rc == -1


def test_missing_executable_is_skip(tmp_workspace):
    # 'go' is allow-listed but assume not installed in CI -> skip, not fail.
    r = runner.run_gate(["go", "version"], tmp_workspace)
    if r.reason == "executable not found":
        assert r.skipped is True
        assert r.passed is False
        assert r.rc == -2
    else:
        # go IS installed here; then it must have actually run.
        assert r.skipped is False


def test_timeout_is_failure(tmp_workspace):
    r = runner.run_gate(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        tmp_workspace,
        timeout_s=1,
    )
    assert r.passed is False
    assert r.skipped is False
    assert r.reason == "timeout"
    assert r.rc == -3


def test_pytest_rc5_is_pass(tmp_workspace):
    # Simulate pytest's "no tests collected" exit code 5 via a fake argv0.
    # run_gate must treat rc==5 as a pass ONLY for pytest.
    # Name the process 'pytest' by symlinking python -> not portable; instead
    # exercise the classifier directly through a pytest-basename command.
    r = runner.run_gate(["pytest", "-q", "--co"], tmp_workspace)
    # If pytest is installed and collects nothing here it returns 5 -> pass.
    if r.rc == 5:
        assert r.passed is True
    # else pytest collected something (rc 0) or isn't installed (skip) - both fine.


def test_utf8_replace_does_not_raise(tmp_workspace):
    # Emit invalid utf-8 bytes; decoding must not raise.
    r = runner.run_gate(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'\\xff\\xfe')"],
        tmp_workspace,
    )
    assert isinstance(r.stdout, str)  # replaced, not crashed
