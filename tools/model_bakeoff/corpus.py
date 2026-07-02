"""Corpus loading + the validate-oracles gate (SPEC §4, §10).

A task is a directory under tasks/ named `<tier>-<slug>` containing prompt.md,
oracle.py (a parameterised pytest oracle importing `solution`), and reference.py
(a known-good solution). validate_oracles runs every reference against its oracle:
a reference that fails its oracle means the oracle is broken, and the run is gated.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import yaml

from . import sandbox
from .models import TaskSpec

_TIERS = ("quick", "standard", "thorough")
_META_KEYS = {"tier", "tags"}


def _tier_of(task_id: str) -> str:
    for tier in _TIERS:
        if task_id.startswith(tier):
            return tier
    return "standard"


def default_tasks_dir() -> str:
    return os.path.join(os.path.dirname(__file__), "tasks")


def _read_meta(task_dir: str, task_id: str) -> tuple[str, tuple[str, ...]]:
    """Optional tasks/<dir>/meta.yaml -> (tier, tags). Absent -> (slug tier, ()).

    Fail loud (SPEC / house rule): malformed YAML, a non-mapping, an unknown key
    (e.g. the `tag:` singular typo), or a bad tier all raise ValueError naming the
    file, rather than silently dropping the task from its intended suite.
    """
    p = os.path.join(task_dir, "meta.yaml")
    if not os.path.isfile(p):
        return _tier_of(task_id), ()
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"malformed meta.yaml in {task_id}: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"meta.yaml in {task_id} must be a mapping")
    unknown = set(data) - _META_KEYS
    if unknown:
        raise ValueError(f"meta.yaml in {task_id}: unknown key(s) {sorted(unknown)}")
    tier = data.get("tier") or _tier_of(task_id)
    if tier not in _TIERS:
        raise ValueError(f"meta.yaml in {task_id}: bad tier {tier!r}")
    tags = tuple(str(t) for t in (data.get("tags") or []))
    return tier, tags


def load(tasks_dir: str | None = None) -> list[TaskSpec]:
    tasks_dir = tasks_dir or default_tasks_dir()
    tasks: list[TaskSpec] = []
    for name in sorted(os.listdir(tasks_dir)):
        d = os.path.join(tasks_dir, name)
        if not os.path.isdir(d):
            continue
        prompt = os.path.join(d, "prompt.md")
        oracle = os.path.join(d, "oracle.py")
        ref = os.path.join(d, "reference.py")
        if all(os.path.isfile(p) for p in (prompt, oracle, ref)):
            tier, tags = _read_meta(d, name)
            tasks.append(TaskSpec(task_id=name, tier=tier, prompt_path=prompt,
                                  oracle_path=oracle, reference_path=ref, tags=tags))
    return tasks


@dataclass
class ValidationResult:
    task_id: str
    ok: bool
    detail: str = ""


def validate_oracles(tasks: list[TaskSpec], timeout_s: int = 60) -> list[ValidationResult]:
    results: list[ValidationResult] = []
    for t in tasks:
        with open(t.reference_path, "r", encoding="utf-8") as f:
            ref_code = f.read()
        sb = sandbox.run(ref_code, t.oracle_path, timeout_s=timeout_s)
        ok = sb.returncode == 0 and not sb.timed_out
        detail = "" if ok else f"rc={sb.returncode} timed_out={sb.timed_out} :: {(sb.stdout + sb.stderr)[-300:]}"
        results.append(ValidationResult(t.task_id, ok, detail))
    return results
