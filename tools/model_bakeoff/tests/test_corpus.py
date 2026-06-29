"""Corpus loading + the validate-oracles gate (SPEC §4). The validation test
runs each reference solution against its oracle in a real sandbox subprocess."""
from __future__ import annotations

from tools.model_bakeoff import corpus


def test_load_finds_seed_tasks():
    tasks = corpus.load()
    ids = {t.task_id for t in tasks}
    assert {"quick-rle", "quick-balanced-brackets",
            "standard-top-k-words", "thorough-merge-intervals"} <= ids


def test_tiers_parsed_from_prefix():
    tasks = {t.task_id: t for t in corpus.load()}
    assert tasks["quick-rle"].tier == "quick"
    assert tasks["standard-top-k-words"].tier == "standard"
    assert tasks["thorough-merge-intervals"].tier == "thorough"


def test_all_reference_solutions_pass_their_oracles():
    # SPEC §4 gate: a reference that fails its oracle is a broken oracle.
    tasks = corpus.load()
    results = corpus.validate_oracles(tasks, timeout_s=30)
    failures = [(r.task_id, r.detail) for r in results if not r.ok]
    assert not failures, f"broken oracle(s): {failures}"
