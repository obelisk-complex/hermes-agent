"""Exercise the REAL loader import path (no sys.path masking).

The per-module unit tests rely on conftest's sys.path insert and so cannot
catch a sibling that wrongly uses a flat ``import X`` instead of the required
relative ``from . import X``. ``real_load_plugin`` reproduces the production
resolution (spec with submodule_search_locations, registered as
hermes_plugins.quality_gate, NO sys.path insert), so a flat-import regression
surfaces here as ModuleNotFoundError — exactly as it would at real plugin load.

At Task 1 there are no siblings yet, so we only assert the package loads under
its real name and exposes __path__. Once __init__.py imports its siblings
(Task 15), this same real-load path transitively proves every sibling import
is relative (see test_register.py).
"""
import sys

from conftest import real_load_plugin


def test_plugin_loads_under_real_package_name():
    mod = real_load_plugin("hermes_plugins.quality_gate")
    assert mod.__name__ == "hermes_plugins.quality_gate"
    assert "hermes_plugins.quality_gate" in sys.modules
    # __path__ must point at the plugin dir so 'from . import X' can resolve.
    assert mod.__path__  # non-empty
