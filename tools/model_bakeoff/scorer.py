"""Turn a raw SandboxResult into a ScoreResult (SPEC §5).

Crucially distinguishes a *collection* error (the code did not import/compile, so
no oracle assertion ran) from a genuine *test failure* (the code ran and the
oracle rejected it), and both from an extraction failure or a timeout. These are
different signals about a model and must not be conflated.
"""
from __future__ import annotations

from typing import Optional

from .models import (
    ERR_COLLECTION,
    ERR_EXTRACTION,
    ERR_TEST_FAIL,
    ERR_TIMEOUT,
    ExtractionResult,
    SandboxResult,
    ScoreResult,
)

# Markers of an import/compile/collection problem, independent of pytest's exit code.
_COLLECTION_MARKERS = (
    "SyntaxError",
    "IndentationError",
    "ImportError",
    "ModuleNotFoundError",
    "errors during collection",
    "ERROR collecting",
)


def score(
    model_key: str,
    task_id: str,
    extraction: ExtractionResult,
    sandbox: Optional[SandboxResult],
) -> ScoreResult:
    if extraction.failed:
        return ScoreResult(model_key, task_id, passed=False,
                           error_type=ERR_EXTRACTION, detail="no code extracted")

    if sandbox is None:
        return ScoreResult(model_key, task_id, passed=False,
                           error_type=ERR_COLLECTION, detail="no sandbox result")

    if sandbox.timed_out:
        return ScoreResult(model_key, task_id, passed=False, error_type=ERR_TIMEOUT,
                           detail=f"timed out after {sandbox.duration_s:.1f}s")

    if sandbox.returncode == 0:
        return ScoreResult(model_key, task_id, passed=True, error_type=None)

    blob = f"{sandbox.stdout}\n{sandbox.stderr}"
    # pytest exit codes: 2 = collection/usage error, 5 = no tests collected.
    collection = (
        sandbox.returncode in (2, 5)
        or any(m in blob for m in _COLLECTION_MARKERS)
        or "no tests ran" in blob.lower()
    )
    if collection:
        return ScoreResult(model_key, task_id, passed=False, error_type=ERR_COLLECTION,
                           detail="code did not import/compile; no assertion ran")

    return ScoreResult(model_key, task_id, passed=False, error_type=ERR_TEST_FAIL,
                       detail="oracle assertions failed")
