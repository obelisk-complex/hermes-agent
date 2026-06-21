"""quality-gate plugin package marker.

The entry module is __init__.py (its register(ctx) is loaded by the Hermes
plugin manager as the package hermes_plugins.quality_gate). Sibling modules
import each other RELATIVELY (from . import registry). Tests load the modules
under that real package name and alias them to bare names via tests/conftest.py
so test files can ``import detect`` while the relative wiring stays intact.
"""
