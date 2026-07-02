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


# --- Phase 1 Task 2: selector resolution (tags + manifests), fail-loud-on-empty ---

def test_default_selector_returns_all_ten_unchanged():          # BACKWARD-COMPAT PIN
    ids = {t.task_id for t in corpus.load()}
    assert ids == {
        "quick-balanced-brackets", "quick-config-flag-parse",
        "quick-overlapping-substring-count", "quick-rle",
        "standard-currency-normalize", "standard-halfopen-merge-intervals",
        "standard-top-k-words", "thorough-expr-eval",
        "thorough-merge-intervals", "thorough-topo-order",
    }


def test_selector_tag_and_all(tmp_path):
    d = str(tmp_path)
    _mktask(d, "quick-a")
    _mktask(d, "thorough-t", "tags: [ai-trap]\n")
    assert {t.task_id for t in corpus.load(d)} == {"quick-a", "thorough-t"}
    assert {t.task_id for t in corpus.load(d, selector="all")} == {"quick-a", "thorough-t"}
    assert [t.task_id for t in corpus.load(d, selector="tag:ai-trap")] == ["thorough-t"]


def test_empty_selection_fails_loud(tmp_path):
    d = str(tmp_path)
    _mktask(d, "quick-a")
    with pytest.raises(ValueError):
        corpus.load(d, selector="tag:nonexistent")              # typo -> 0 tasks -> raise


def test_selector_manifest_order(tmp_path):
    d = str(tmp_path)
    for n in ("quick-a", "quick-b", "thorough-t"):
        _mktask(d, n)
    sd = os.path.join(d, "suites")
    os.makedirs(sd)
    open(os.path.join(sd, "mix.yaml"), "w").write("name: mix\nchallenges: [thorough-t, quick-a]\n")
    got = [t.task_id for t in corpus.load(d, selector="mix", suites_dir=sd)]
    assert got == ["thorough-t", "quick-a"]                     # manifest order preserved


def test_manifest_duplicate_slug_raises(tmp_path):
    d = str(tmp_path)
    for n in ("quick-a", "quick-b"):
        _mktask(d, n)
    sd = os.path.join(d, "suites")
    os.makedirs(sd)
    open(os.path.join(sd, "dup.yaml"), "w").write("name: dup\nchallenges: [quick-a, quick-a, quick-b]\n")
    with pytest.raises(ValueError):
        corpus.load(d, selector="dup", suites_dir=sd)


def test_unknown_manifest_raises(tmp_path):
    d = str(tmp_path)
    _mktask(d, "quick-a")
    sd = os.path.join(d, "suites")
    os.makedirs(sd)
    with pytest.raises(ValueError):
        corpus.load(d, selector="nope", suites_dir=sd)


def test_load_manifest_rejects_reserved(tmp_path):              # defence-in-depth guard fires
    sd = str(tmp_path)
    open(os.path.join(sd, "all.yaml"), "w").write("name: all\nchallenges: [x]\n")
    with pytest.raises(ValueError):
        corpus._load_manifest("all", sd)


def test_list_suites_excludes_reserved(tmp_path):
    sd = str(tmp_path)
    for f in ("good.yaml", "all.yaml", "tag:x.yaml"):
        open(os.path.join(sd, f), "w").write("name: x\nchallenges: [y]\n")
    assert corpus.list_suites(sd) == ["good"]                  # reserved stems hidden, sorted
