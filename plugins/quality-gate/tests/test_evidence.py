import json

import evidence
import runner


def _fake_run(passed=True):
    return runner.GateRun(
        cmd=["pytest", "-q"], cwd="/tmp", rc=0 if passed else 1,
        stdout="x" * 10, stderr="", duration_s=0.1,
        passed=passed, skipped=False, reason="",
    )


def test_evidence_dir_writes_gitignore(tmp_workspace):
    d = evidence.evidence_dir(tmp_workspace)
    assert d == tmp_workspace / ".hermes" / "gate-runs"
    assert d.is_dir()
    gi = d / ".gitignore"
    assert gi.read_text(encoding="utf-8").strip() == "*"


def test_record_run_writes_json(tmp_workspace):
    p = evidence.record_run(
        tmp_workspace, _fake_run(passed=False),
        kind="test", stack="python", tier="standard", task_id="t-1",
    )
    assert p.exists()
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["kind"] == "test"
    assert data["stack"] == "python"
    assert data["tier"] == "standard"
    assert data["task_id"] == "t-1"
    assert data["passed"] is False
    assert data["rc"] == 1
    assert data["cmd"] == ["pytest", "-q"]


def test_record_truncates_large_output(tmp_workspace):
    big = runner.GateRun(
        cmd=["pytest"], cwd="/tmp", rc=1, stdout="y" * 20000, stderr="",
        duration_s=0.1, passed=False, skipped=False, reason="",
    )
    p = evidence.record_run(tmp_workspace, big, kind="test", stack="python", tier="quick")
    data = json.loads(p.read_text(encoding="utf-8"))
    assert len(data["stdout"]) <= 8000


def test_two_rapid_runs_do_not_collide(tmp_workspace):
    # Two record_run calls for the SAME stack+kind+task in immediate succession
    # (simulating a requeue retry) must produce two distinct files, not overwrite.
    r = _fake_run()
    p1 = evidence.record_run(tmp_workspace, r, kind="test", stack="python",
                             tier="standard", task_id="task-12345678")
    p2 = evidence.record_run(tmp_workspace, r, kind="test", stack="python",
                             tier="standard", task_id="task-12345678")
    assert p1 != p2
    assert p1.exists() and p2.exists()
    d = evidence.evidence_dir(tmp_workspace)
    # Two evidence JSONs present (the .gitignore is not a .json).
    jsons = sorted(d.glob("*.json"))
    assert len(jsons) == 2
