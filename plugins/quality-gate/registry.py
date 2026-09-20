"""Gate registry: per-stack lint/test/typecheck/build commands.

Commands are stored as argv LISTS (never shell strings) and the first token
of every command must be in ``ALLOWLIST``. The gate runner uses these argv
lists with ``shell=False`` so an arbitrary string can never be eval'd.
"""
from __future__ import annotations

import os
import re
from typing import Dict, List

GATE_KINDS = ("lint", "test", "typecheck", "build")

# Executables the gate is permitted to invoke. Matched on basename.
ALLOWLIST = frozenset({
    "python", "python3", "ruff", "flake8", "mypy", "pyright", "pytest",
    "npm", "npx", "node", "eslint", "tsc",
    "cargo", "go", "gofmt",
})

# Version-suffixed Python interpreters (python3.11, python3.14, ...). Anchored
# so a crafted lookalike (python3.11-evil, python3x) cannot match. This keeps
# the allow-list robust to however the host names sys.executable.
_PY_VERSIONED = re.compile(r"^python3\.\d+$")

# stack -> kind -> list of argv lists. A kind may have zero commands (a stack
# with no typecheck simply has no entry, which the tier layer treats as "skip").
DEFAULT_GATES: Dict[str, Dict[str, List[List[str]]]] = {
    "python": {
        "lint": [["ruff", "check", "."]],
        "test": [["pytest", "-q"]],
        "typecheck": [["mypy", "."]],
        "build": [],
    },
    "node": {
        "lint": [["npx", "--no-install", "eslint", "."]],
        "test": [["npm", "test", "--silent"]],
        "typecheck": [["npx", "--no-install", "tsc", "--noEmit"]],
        "build": [["npm", "run", "build", "--silent"]],
    },
    "rust": {
        "lint": [["cargo", "clippy", "--quiet"]],
        "test": [["cargo", "test", "--quiet"]],
        "typecheck": [["cargo", "check", "--quiet"]],
        "build": [["cargo", "build", "--quiet"]],
    },
    "go": {
        "lint": [["go", "vet", "./..."]],
        "test": [["go", "test", "./..."]],
        "typecheck": [["go", "build", "./..."]],
        "build": [["go", "build", "./..."]],
    },
}


def gates_for(stack: str) -> Dict[str, List[List[str]]]:
    """Return the gate map for *stack* (empty dict if unknown)."""
    return DEFAULT_GATES.get(stack, {})


def is_allowed(cmd: List[str]) -> bool:
    """True iff *cmd* is non-empty and its executable is allow-listed.

    Accepts an exact basename match, OR a version-suffixed Python interpreter
    (python3.x) so sys.executable resolves regardless of host naming.
    """
    if not cmd:
        return False
    exe = os.path.basename(cmd[0])
    return exe in ALLOWLIST or bool(_PY_VERSIONED.match(exe))


def validate_registry() -> None:
    """Raise ValueError if any DEFAULT_GATES command is not allow-listed."""
    for stack, kinds in DEFAULT_GATES.items():
        for kind, cmds in kinds.items():
            for cmd in cmds:
                if not is_allowed(cmd):
                    raise ValueError(
                        f"un-allowlisted command in registry: "
                        f"{stack}/{kind}: {cmd!r}"
                    )
