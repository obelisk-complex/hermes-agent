"""Stack detection for the quality gate.

Scans a workspace for language markers, skipping noise dirs that would
otherwise create phantom stacks (the classic trap: ``.pytest_cache/README.md``
looking like a "docs" project, or a ``package.json`` deep in ``node_modules``).
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Union

# Directory names we never descend into. .pytest_cache is here specifically
# to dodge the README.md phantom-"docs"-stack trap; node_modules so a vendored
# package.json never registers a node stack.
_SKIP = frozenset({
    ".git", ".hermes", ".pytest_cache", "node_modules", ".venv", "venv",
    "__pycache__", ".mypy_cache", ".ruff_cache", ".tox", "dist", "build",
    ".gradle", "target", ".idea",
})

# Marker filenames -> stack. Checked at the workspace root and one level down.
_MARKERS = {
    "pyproject.toml": "python",
    "setup.py": "python",
    "setup.cfg": "python",
    "requirements.txt": "python",
    "package.json": "node",
    "Cargo.toml": "rust",
    "go.mod": "go",
}


def _scan_dir(d: Path, found: set) -> None:
    """Add any stacks whose markers appear directly in *d*."""
    try:
        entries = list(d.iterdir())
    except OSError:
        return
    for entry in entries:
        name = entry.name
        if entry.is_file():
            stack = _MARKERS.get(name)
            if stack:
                found.add(stack)
            elif name.endswith(".py"):
                found.add("python")


def detect_stacks(workspace: Union[str, Path]) -> List[str]:
    """Return sorted detected stack names under *workspace*.

    Scans the root and its immediate (non-skipped) subdirectories. Skipped
    dirs (``_SKIP``) are never inspected, so vendored/cache files cannot
    create phantom stacks.
    """
    root = Path(workspace)
    found: set = set()
    _scan_dir(root, found)
    try:
        children = list(root.iterdir())
    except OSError:
        children = []
    for child in children:
        if child.is_dir() and child.name not in _SKIP:
            _scan_dir(child, found)
    return sorted(found)
