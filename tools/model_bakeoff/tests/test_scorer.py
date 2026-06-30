"""scorer.score() classification (SPEC §5). Offline, pure."""
from __future__ import annotations

from tools.model_bakeoff import scorer
from tools.model_bakeoff.models import (
    ERR_COLLECTION,
    ERR_EXTRACTION,
    ERR_OUTPUT_CAP,
    ERR_TEST_FAIL,
    ERR_TIMEOUT,
    ExtractionResult,
    SandboxResult,
)

OK_EXTRACTION = ExtractionResult(code="def f(): return 1", method="fenced-python")


def _score(extraction, sandbox):
    return scorer.score("m", "t1", extraction, sandbox)


def test_extraction_failure_is_its_own_error_type():
    r = _score(ExtractionResult(code="", failed=True, method="none"),
               SandboxResult(returncode=0))
    assert not r.passed
    assert r.error_type == ERR_EXTRACTION


def test_pass_on_returncode_zero():
    r = _score(OK_EXTRACTION, SandboxResult(returncode=0, stdout="2 passed"))
    assert r.passed
    assert r.error_type is None


def test_test_failure_on_returncode_one():
    r = _score(OK_EXTRACTION, SandboxResult(returncode=1, stdout="1 failed, 1 passed"))
    assert not r.passed
    assert r.error_type == ERR_TEST_FAIL


def test_collection_error_on_returncode_two():
    r = _score(OK_EXTRACTION, SandboxResult(returncode=2, stderr="errors during collection"))
    assert not r.passed
    assert r.error_type == ERR_COLLECTION


def test_no_tests_collected_is_collection_error():
    r = _score(OK_EXTRACTION, SandboxResult(returncode=5, stdout="no tests ran"))
    assert r.error_type == ERR_COLLECTION


def test_syntax_error_is_collection_even_if_exit_one():
    r = _score(OK_EXTRACTION, SandboxResult(returncode=1, stderr="SyntaxError: invalid syntax"))
    assert r.error_type == ERR_COLLECTION


def test_import_error_is_collection():
    r = _score(OK_EXTRACTION, SandboxResult(returncode=1, stdout="E   ModuleNotFoundError: no module named x"))
    assert r.error_type == ERR_COLLECTION


def test_timeout_is_its_own_error_type():
    r = _score(OK_EXTRACTION, SandboxResult(returncode=-9, timed_out=True, duration_s=300.0))
    assert not r.passed
    assert r.error_type == ERR_TIMEOUT


def test_missing_sandbox_is_collection_error():
    r = _score(OK_EXTRACTION, None)
    assert not r.passed
    assert r.error_type == ERR_COLLECTION


def test_truncated_output_is_its_own_error_not_test_failure():
    # a SIGXFSZ kill has a non-zero returncode that would otherwise read as ERR_TEST_FAIL
    r = _score(OK_EXTRACTION, SandboxResult(returncode=-25, truncated=True, stdout="AAAA"))
    assert not r.passed
    assert r.error_type == ERR_OUTPUT_CAP


def test_timeout_takes_precedence_over_truncation():
    r = _score(OK_EXTRACTION, SandboxResult(returncode=-9, timed_out=True, truncated=True))
    assert r.error_type == ERR_TIMEOUT
