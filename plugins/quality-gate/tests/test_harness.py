"""Proves the plugin-local pytest harness collects and imports correctly.

If the hyphenated-dir + __init__.py trap is unmitigated, importlib mode +
the conftest path bootstrap below fail and this test never collects.
"""
import sys
from pathlib import Path


def test_conftest_registers_package():
    # The round-1 conftest does NOT insert the plugin dir on sys.path; it loads
    # modules under hermes_plugins.quality_gate.<name> and aliases them to bare
    # names in sys.modules. So the postcondition is that the package itself is
    # registered (its __path__ points at the plugin dir for relative imports).
    assert "hermes_plugins.quality_gate" in sys.modules
    pkg = sys.modules["hermes_plugins.quality_gate"]
    plugin_dir = Path(__file__).resolve().parent.parent
    assert str(plugin_dir) in list(pkg.__path__)


def test_tmp_workspace_fixture(tmp_workspace):
    assert tmp_workspace.is_dir()
    # writable
    (tmp_workspace / "probe.txt").write_text("ok", encoding="utf-8")
    assert (tmp_workspace / "probe.txt").read_text(encoding="utf-8") == "ok"
