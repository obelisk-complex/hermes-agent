import sys

import gate
import registry
import runner


def test_no_stack_passes(tmp_workspace):
    res = gate.evaluate_completion(tmp_workspace, "standard", check_hygiene=False)
    assert res.passed is True
    assert "no recognised stack" in res.summary
    assert res.stacks == []


def test_failing_gate_blocks(tmp_workspace, monkeypatch):
    (tmp_workspace / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    # Force the python 'test' command to a deterministic failing command.
    monkeypatch.setattr(
        registry, "DEFAULT_GATES",
        {"python": {"lint": [], "test": [[sys.executable, "-c", "import sys; sys.exit(1)"]]}},
    )
    res = gate.evaluate_completion(tmp_workspace, "standard", check_hygiene=False)
    assert res.passed is False
    assert "FAIL" in res.summary
    # Evidence written.
    assert (tmp_workspace / ".hermes" / "gate-runs").is_dir()


def test_passing_gate_allows(tmp_workspace, monkeypatch):
    (tmp_workspace / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    monkeypatch.setattr(
        registry, "DEFAULT_GATES",
        {"python": {"lint": [], "test": [[sys.executable, "-c", "print('ok')"]]}},
    )
    res = gate.evaluate_completion(tmp_workspace, "standard", check_hygiene=False)
    assert res.passed is True


def test_skipped_run_does_not_fail_gate(tmp_workspace, monkeypatch):
    (tmp_workspace / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    # A not-allow-listed command -> skipped -> must NOT fail the gate.
    monkeypatch.setattr(
        registry, "DEFAULT_GATES",
        {"python": {"lint": [["definitely-not-allowed"]], "test": []}},
    )
    res = gate.evaluate_completion(tmp_workspace, "quick", check_hygiene=False)
    assert res.passed is True
    assert any(r.skipped for r in res.runs)


def test_scratch_dir_with_code_is_gated(tmp_workspace, monkeypatch):
    # workspace_kind="scratch" is the DEFAULT for kanban tasks. A scratch dir
    # that CONTAINS code (a pyproject.toml here) must NOT silently skip: the
    # gate must detect the python stack and actually run + block on failure.
    (tmp_workspace / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    monkeypatch.setattr(
        registry, "DEFAULT_GATES",
        {"python": {"lint": [], "test": [[sys.executable, "-c", "import sys; sys.exit(1)"]]}},
    )
    res = gate.evaluate_completion(tmp_workspace, "standard", check_hygiene=False)
    assert res.stacks == ["python"]      # detected, not skipped
    assert res.passed is False           # actually ran and failed


def test_hygiene_default_non_repo_passes(tmp_workspace, monkeypatch):
    # The default is check_hygiene=True. A non-repo workspace with a stack must
    # still PASS hygiene (non-repo => clean) - exercises the default code path
    # that every other gate test bypasses with check_hygiene=False.
    (tmp_workspace / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    monkeypatch.setattr(
        registry, "DEFAULT_GATES",
        {"python": {"lint": [], "test": [[sys.executable, "-c", "print('ok')"]]}},
    )
    res = gate.evaluate_completion(tmp_workspace, "standard")  # default check_hygiene=True
    assert res.passed is True
    assert res.hygiene_clean is True


def test_total_budget_skips_remaining_gates(tmp_workspace, monkeypatch):
    # The budget is checked BEFORE each command. With max_total_s=0.0 every
    # command is over budget on entry, so each is recorded as a budget skip and
    # NONE actually executes (the "should not run" command must not run).
    (tmp_workspace / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    monkeypatch.setattr(
        registry, "DEFAULT_GATES",
        {"python": {
            "lint": [[sys.executable, "-c", "print('lint')"]],
            "test": [[sys.executable, "-c", "import sys; print('should not run'); sys.exit(1)"]],
        }},
    )
    # standard runs lint+test; budget 0.0 => both are budget-skipped, so the
    # failing 'test' command never runs and cannot fail the gate.
    res = gate.evaluate_completion(
        tmp_workspace, "standard", check_hygiene=False, max_total_s=0.0,
    )
    budget_skips = [r for r in res.runs if r.reason == "total budget exceeded"]
    assert len(budget_skips) == 2          # both lint and test skipped for budget
    assert all(r.skipped and r.rc == -4 for r in budget_skips)
    # Budget skips are inability-to-run; with no real run executed the gate
    # passes (fail-open on inability, never on a command that did not run).
    assert res.passed is True
