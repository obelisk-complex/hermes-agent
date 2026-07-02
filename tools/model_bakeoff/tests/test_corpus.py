"""Corpus loading + the validate-oracles gate (SPEC §4). The validation test
runs each reference solution against its oracle in a real sandbox subprocess."""
from __future__ import annotations

import os

import pytest

from tools.model_bakeoff import corpus


def _mktask(d, name, meta=None):
    """Create a minimal valid task dir (optionally with a meta.yaml) under d."""
    p = os.path.join(d, name)
    os.makedirs(p)
    for f in ("prompt.md", "oracle.py", "reference.py"):
        open(os.path.join(p, f), "w").write("x")
    if meta is not None:
        open(os.path.join(p, "meta.yaml"), "w").write(meta)
    return p


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


# --- Phase 1 Task 1: meta.yaml (tags + tier override) with a fail-loud unknown-key guard ---

def test_meta_yaml_tags_and_tier_override(tmp_path):
    d = str(tmp_path)
    _mktask(d, "quick-a")                                     # no meta -> tier quick, tags ()
    _mktask(d, "standard-b", "tier: thorough\ntags: [ai-trap, edge]\n")
    tasks = {t.task_id: t for t in corpus.load(d)}
    assert tasks["quick-a"].tags == () and tasks["quick-a"].tier == "quick"
    assert tasks["standard-b"].tier == "thorough"
    assert tasks["standard-b"].tags == ("ai-trap", "edge")


def test_malformed_meta_raises(tmp_path):
    d = str(tmp_path)
    _mktask(d, "quick-c", "tags: [unclosed\n")
    with pytest.raises(ValueError):
        corpus.load(d)


def test_meta_unknown_key_raises(tmp_path):
    d = str(tmp_path)
    _mktask(d, "quick-d", "tag: [ai-trap]\n")                 # singular typo -> must fail loud
    with pytest.raises(ValueError):
        corpus.load(d)


def test_meta_bad_tier_raises(tmp_path):
    d = str(tmp_path)
    _mktask(d, "quick-e", "tier: gigantic\n")
    with pytest.raises(ValueError):
        corpus.load(d)
