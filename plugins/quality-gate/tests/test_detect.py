import detect


def test_python_via_pyproject(tmp_workspace):
    (tmp_workspace / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    assert detect.detect_stacks(tmp_workspace) == ["python"]


def test_node_via_package_json(tmp_workspace):
    (tmp_workspace / "package.json").write_text("{}", encoding="utf-8")
    assert detect.detect_stacks(tmp_workspace) == ["node"]


def test_multi_stack_sorted(tmp_workspace):
    (tmp_workspace / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    (tmp_workspace / "go.mod").write_text("module x\n", encoding="utf-8")
    assert detect.detect_stacks(tmp_workspace) == ["go", "rust"]


def test_pytest_cache_readme_does_not_create_docs_stack(tmp_workspace):
    # The phantom-"docs"-stack trap: .pytest_cache/README.md must be skipped
    # so it never triggers a stack. With no real markers, result is empty.
    cache = tmp_workspace / ".pytest_cache"
    cache.mkdir()
    (cache / "README.md").write_text("# pytest cache\n", encoding="utf-8")
    (cache / "CACHEDIR.TAG").write_text("Signature\n", encoding="utf-8")
    assert detect.detect_stacks(tmp_workspace) == []


def test_node_modules_package_json_ignored(tmp_workspace):
    # A package.json buried in node_modules must NOT register a node stack.
    nm = tmp_workspace / "node_modules" / "left-pad"
    nm.mkdir(parents=True)
    (nm / "package.json").write_text("{}", encoding="utf-8")
    assert detect.detect_stacks(tmp_workspace) == []


def test_loose_python_file(tmp_workspace):
    (tmp_workspace / "main.py").write_text("print(1)\n", encoding="utf-8")
    assert detect.detect_stacks(tmp_workspace) == ["python"]


def test_python_file_one_level_down(tmp_workspace):
    # A .py file in a (non-skipped) immediate subdir must register python:
    # detect scans the root AND one level down. src/helper.py -> ["python"].
    src = tmp_workspace / "src"
    src.mkdir()
    (src / "helper.py").write_text("x = 1\n", encoding="utf-8")
    assert detect.detect_stacks(tmp_workspace) == ["python"]
