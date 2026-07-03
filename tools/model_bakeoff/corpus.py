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


def default_suites_dir() -> str:
    return os.path.join(os.path.dirname(__file__), "suites")


def list_suites(suites_dir: str | None = None) -> list[str]:
    """Selectable manifest names (sorted). Reserved stems (`all`, `tag:*`) are hidden
    because they can never be selected as a manifest; validate_suites flags them loudly."""
    sd = suites_dir or default_suites_dir()
    if not os.path.isdir(sd):
        return []
    stems = [os.path.splitext(f)[0] for f in sorted(os.listdir(sd)) if f.endswith(".yaml")]
    return [s for s in stems if s != "all" and not s.startswith("tag:")]


def _load_manifest(name: str, suites_dir: str | None) -> list[str]:
    """Ordered challenge slugs from suites/<name>.yaml. Fail loud on reserved name,
    missing file, empty challenge list, or a duplicated slug (silent double-counting
    would skew a model's aggregates and CI)."""
    if name == "all" or name.startswith("tag:"):
        raise ValueError(f"suite name {name!r} is reserved")
    sd = suites_dir or default_suites_dir()
    p = os.path.join(sd, f"{name}.yaml")
    if not os.path.isfile(p):
        raise ValueError(f"unknown suite {name!r} (no {p})")
    with open(p, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    names = list(data.get("challenges") or [])
    if not names:
        raise ValueError(f"suite {name!r} has no challenges")
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise ValueError(f"suite {name!r} lists duplicate challenge(s): {dupes}")
    return names


def load(tasks_dir: str | None = None, selector: str | None = None,
         suites_dir: str | None = None) -> list[TaskSpec]:
    """Discover tasks; optionally narrow by a suite selector.

    selector: None / "" / "all" -> whole corpus (unchanged default). "tag:<t>" ->
    challenges whose tags include <t> (sorted). "<name>" -> the named manifest's
    challenges in manifest order. An explicit selector resolving to 0 tasks fails
    loud (a typo'd tag must never silently run zero tasks).
    """
    tasks_dir = tasks_dir or default_tasks_dir()
    all_tasks: list[TaskSpec] = []
    for name in sorted(os.listdir(tasks_dir)):
        d = os.path.join(tasks_dir, name)
        if not os.path.isdir(d):
            continue
        prompt = os.path.join(d, "prompt.md")
        oracle = os.path.join(d, "oracle.py")
        ref = os.path.join(d, "reference.py")
        if all(os.path.isfile(p) for p in (prompt, oracle, ref)):
            tier, tags = _read_meta(d, name)
            all_tasks.append(TaskSpec(task_id=name, tier=tier, prompt_path=prompt,
                                      oracle_path=oracle, reference_path=ref, tags=tags))

    if selector in (None, "", "all"):
        return all_tasks
    by_id = {t.task_id: t for t in all_tasks}
    if selector.startswith("tag:"):
        tag = selector[4:]
        selected = [t for t in all_tasks if tag in t.tags]        # all_tasks is already sorted
    else:
        names = _load_manifest(selector, suites_dir)              # raises on missing/dup/reserved
        missing = [n for n in names if n not in by_id]
        if missing:
            raise ValueError(f"suite {selector!r} references unknown challenges: {missing}")
        selected = [by_id[n] for n in names]                      # manifest order
    if not selected:
        raise ValueError(f"suite {selector!r} resolved to 0 tasks")   # FAIL LOUD
    return selected


@dataclass
class ValidationResult:
    task_id: str
    ok: bool
    detail: str = ""


def validate_suites(tasks_dir: str | None = None,
                    suites_dir: str | None = None) -> list[ValidationResult]:
    """Gate every manifest in suites_dir. Walks the RAW directory listing (not
    list_suites) so a reserved-name file (all.yaml / tag:*.yaml) is flagged ok=False
    ("reserved; can never be selected") rather than silently omitted. A manifest that
    fails to resolve (missing/dup/unknown slug, empty) is reported ok=False, not crashed.
    ValidationResult.task_id holds the SUITE name here."""
    sd = suites_dir or default_suites_dir()
    results: list[ValidationResult] = []
    if not os.path.isdir(sd):
        return results
    for f in sorted(os.listdir(sd)):
        if not f.endswith(".yaml"):
            continue
        name = os.path.splitext(f)[0]
        if name == "all" or name.startswith("tag:"):
            results.append(ValidationResult(name, False, "reserved name; can never be selected"))
            continue
        try:
            tasks = load(tasks_dir, selector=name, suites_dir=sd)
            results.append(ValidationResult(name, True, f"{len(tasks)} task(s)"))
        except Exception as e:      # noqa: BLE001 - report the failure, don't crash the gate
            results.append(ValidationResult(name, False, str(e)))
    return results


def assert_disjoint(selector_a: str, selector_b: str, tasks_dir: str | None = None,
                    suites_dir: str | None = None) -> None:
    """Leakage guard: raise ValueError listing the overlapping task_ids if the two
    selectors share any challenge. Used to prove a dev suite is disjoint from a scored
    suite before tuning (PIPELINE-DESIGN sub-project C)."""
    a = {t.task_id for t in load(tasks_dir, selector_a, suites_dir)}
    b = {t.task_id for t in load(tasks_dir, selector_b, suites_dir)}
    overlap = sorted(a & b)
    if overlap:
        raise ValueError(f"suites {selector_a!r} and {selector_b!r} overlap: {overlap}")


def assert_disjoint_dirs(dir_a: str, dir_b: str | None = None, *,
                         ids_b: set[str] | None = None) -> None:
    """Cross-DIRECTORY leakage guard (sub-project D): raise ValueError listing the overlapping
    task_ids if `dir_a`'s corpus shares any id with `dir_b`'s corpus (when `dir_b` is a real dir) or
    with the explicit `ids_b` set (the dev dir is gone; ids come from dev_corpus.json["dev_tasks"]).
    Complements assert_disjoint (which compares two SELECTORS inside one dir): D evaluates on the
    scored tasks/ dir while the tuner trained on a separate dev_tasks/ dir. Raises if neither dir_b nor
    ids_b is given (nothing to compare against); the caller decides how to handle a fully-absent dev
    corpus (D warns and does not block)."""
    a = {t.task_id for t in load(dir_a)}
    if dir_b is not None:
        b = {t.task_id for t in load(dir_b)}
    elif ids_b is not None:
        b = set(ids_b)
    else:
        raise ValueError("assert_disjoint_dirs needs either dir_b (a directory) or ids_b (an id set) "
                         "to compare against; both were None")
    overlap = sorted(a & b)
    if overlap:
        raise ValueError(f"scored dir {dir_a!r} leaks into the dev corpus; shared task_id(s): {overlap}")


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
