"""Bootstrap: load the plugin under its REAL package name, then alias each
submodule to its flat name so per-module tests can ``import detect`` etc.

WHY THIS IS NOT A PLAIN sys.path INSERT
---------------------------------------
The plugin's modules import their siblings with RELATIVE imports
(``from . import registry``) because that is the only thing that works at real
load: ``_load_directory_module`` (plugins.py) loads ``__init__.py`` as
``hermes_plugins.quality_gate`` with ``__path__=[plugin_dir]`` but does NOT put
the dir on ``sys.path``. A flat ``import registry`` inside a sibling therefore
raises ``ModuleNotFoundError`` in production. A naive test ``sys.path`` insert
that let tests do ``import runner`` would then EXPLODE on the module's own
``from . import registry`` line (``attempted relative import with no known
parent package``).

So instead we load the plugin exactly as the loader does (``real_load_plugin``)
under ``hermes_plugins.quality_gate`` (NO sys.path insert), then register each
already-loaded submodule ALSO under its bare name in ``sys.modules``. Test
files keep writing ``import registry`` / ``import gate`` and get the SAME module
object the package uses — which also means ``monkeypatch.setattr(registry,
"DEFAULT_GATES", ...)`` patches the exact object the gate reads (no module
identity split). This is the contract the unit tests rely on AND it exercises
the real relative-import wiring.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
import types
from pathlib import Path

import pytest

_PLUGIN_DIR = Path(__file__).resolve().parent.parent
_PKG = "hermes_plugins.quality_gate"

# Every non-__init__ module, in dependency order (leaves first). Aliased to
# flat names after the package loads so tests can import them bare.
_SUBMODULES = (
    "detect", "registry", "tiers", "runner", "githygiene", "evidence",
    "gate", "classify", "ladder", "notify",
    "spawn_hook", "blocked_hook", "completion_hook",
)


def real_load_plugin(pkg_name: str = _PKG):
    """Load the plugin the way ``_load_directory_module`` does — NO sys.path.

    Builds a spec with ``submodule_search_locations=[plugin_dir]``, ensures the
    ``hermes_plugins`` namespace parent exists, registers the package in
    ``sys.modules`` under *pkg_name*, and execs ``__init__.py``. Because no dir
    is added to ``sys.path``, a sibling that wrongly uses a flat ``import X``
    raises ``ModuleNotFoundError`` exactly as at real load.
    """
    parent = pkg_name.rsplit(".", 1)[0] if "." in pkg_name else ""
    if parent and parent not in sys.modules:
        ns = types.ModuleType(parent)
        ns.__path__ = []  # namespace package
        ns.__package__ = parent
        sys.modules[parent] = ns

    init_file = _PLUGIN_DIR / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        pkg_name, init_file, submodule_search_locations=[str(_PLUGIN_DIR)],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = pkg_name
    mod.__path__ = [str(_PLUGIN_DIR)]
    sys.modules[pkg_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _bootstrap_flat_aliases() -> None:
    """Load each submodule as ``hermes_plugins.quality_gate.<name>`` and also
    bind it under its bare ``<name>`` so test files can ``import <name>``.

    We import submodules directly (not via the entry ``__init__`` register, which
    pulls hermes_cli) so leaf-module tests do not require the whole agent. Any
    submodule that does not import cleanly here is a real relative-import bug.
    """
    # Ensure the parent namespace + a package object for quality_gate exist so
    # ``importlib.import_module("hermes_plugins.quality_gate.detect")`` resolves
    # without executing the entry __init__ (which needs hermes_cli).
    parent = "hermes_plugins"
    if parent not in sys.modules:
        ns = types.ModuleType(parent)
        ns.__path__ = []
        ns.__package__ = parent
        sys.modules[parent] = ns
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_PLUGIN_DIR)]
        pkg.__package__ = _PKG
        sys.modules[_PKG] = pkg
    for name in _SUBMODULES:
        full = f"{_PKG}.{name}"
        try:
            sub = importlib.import_module(full)
        except ModuleNotFoundError:
            # The submodule may not exist yet (TDD: earlier tasks run before
            # later modules are written). Skip; the test that needs it will fail
            # loudly on its own ``import <name>`` if it is genuinely missing.
            continue
        sys.modules[name] = sub


_bootstrap_flat_aliases()


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    """A fresh, writable workspace dir for gate/evidence tests."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws
