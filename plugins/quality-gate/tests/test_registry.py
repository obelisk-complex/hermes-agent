import registry


def test_gate_kinds():
    assert registry.GATE_KINDS == ("lint", "test", "typecheck", "build")


def test_python_gates_present():
    g = registry.gates_for("python")
    assert ["pytest", "-q"] in g["test"]
    assert any(cmd[0] in {"ruff", "flake8"} for cmd in g["lint"])


def test_unknown_stack_empty():
    assert registry.gates_for("cobol") == {}


def test_is_allowed_basename():
    assert registry.is_allowed(["pytest", "-q"]) is True
    assert registry.is_allowed(["/usr/bin/python", "-c", "x"]) is True


def test_is_allowed_versioned_python():
    # sys.executable may be python3.11 / python3.14 etc. — must be allowed.
    import sys, os
    assert registry.is_allowed([sys.executable, "-c", "x"]) is True
    assert registry.is_allowed(["/usr/bin/python3.14", "-c", "x"]) is True
    assert registry.is_allowed(["python3.11"]) is True
    # but a crafted lookalike must NOT slip through the version matcher.
    assert registry.is_allowed(["python3.11-evil"]) is False
    assert registry.is_allowed(["python3x"]) is False


def test_is_allowed_rejects_unlisted():
    assert registry.is_allowed(["rm", "-rf", "/"]) is False
    assert registry.is_allowed([]) is False
    assert registry.is_allowed(["bash", "-c", "evil"]) is False


def test_every_default_command_is_allowlisted():
    # Self-test: no DEFAULT_GATES command may smuggle an un-vetted executable.
    registry.validate_registry()  # must not raise
    for stack, kinds in registry.DEFAULT_GATES.items():
        for kind, cmds in kinds.items():
            for cmd in cmds:
                assert registry.is_allowed(cmd), (stack, kind, cmd)
