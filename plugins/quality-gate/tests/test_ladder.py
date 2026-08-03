import ladder


def test_default_ladder_used_when_absent():
    assert ladder.load_ladder(None) == ladder.DEFAULT_LADDER
    assert ladder.load_ladder({}) == ladder.DEFAULT_LADDER
    assert ladder.load_ladder({"quality_gate": {}}) == ladder.DEFAULT_LADDER


def test_config_ladder_overrides_and_dedupes():
    cfg = {"quality_gate": {"model_ladder": ["a", "b", "b", "c"]}}
    assert ladder.load_ladder(cfg) == ["a", "b", "c"]


def test_config_ladder_ignored_when_not_list_of_str():
    assert ladder.load_ladder({"quality_gate": {"model_ladder": "nope"}}) == ladder.DEFAULT_LADDER
    assert ladder.load_ladder({"quality_gate": {"model_ladder": []}}) == ladder.DEFAULT_LADDER


def test_next_rung_walks_up():
    lad = ["a", "b", "c"]
    assert ladder.next_rung("a", lad) == "b"
    assert ladder.next_rung("b", lad) == "c"
    assert ladder.next_rung("c", lad) is None  # already top


def test_next_rung_unknown_current_returns_first():
    assert ladder.next_rung("zzz", ["a", "b"]) == "a"
    assert ladder.next_rung(None, ["a", "b"]) == "a"


def test_next_rung_unknown_nonnull_current_warns(caplog):
    # A non-None current that is NOT on the ladder is a likely misconfig and a
    # downgrade-escalation; it must be logged at WARNING (fail-loud), while a
    # fresh card (current is None) must NOT warn.
    import logging
    with caplog.at_level(logging.WARNING):
        assert ladder.next_rung("opus-not-on-ladder", ["a", "b"]) == "a"
    assert any("not on the configured ladder" in r.message or "not on" in r.message.lower()
               for r in caplog.records)
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        assert ladder.next_rung(None, ["a", "b"]) == "a"
    assert caplog.records == []  # fresh card: no warning


def test_initial_rung_capped_one_below_top():
    # 3-rung ladder -> start at index 1 ("b") so one escalation remains.
    assert ladder.initial_rung(["a", "b", "c"]) == "b"
    # 1-rung ladder -> that single rung.
    assert ladder.initial_rung(["only"]) == "only"
    # 2-rung ladder -> index 0.
    assert ladder.initial_rung(["a", "b"]) == "a"


def test_initial_rung_for_tier_spreads_on_4_rung():
    lad = ["w", "x", "y", "z"]  # cap_idx = len-2 = 2; top "z" only via escalation
    assert ladder.initial_rung_for_tier(lad, "quick") == "w"     # ladder[0]
    assert ladder.initial_rung_for_tier(lad, "standard") == "x"  # ladder[min((4-1)//2=1, 2)]
    assert ladder.initial_rung_for_tier(lad, "thorough") == "y"  # ladder[cap_idx=2]
    # Invariant: no tier ever starts at the top rung, so an escalation remains.
    for t in ("quick", "standard", "thorough"):
        assert ladder.initial_rung_for_tier(lad, t) != lad[-1]


def test_initial_rung_for_tier_compresses_on_short_ladders():
    # Intentional: short ladders compress (documented in the function).
    # len==2 -> all tiers collapse onto ladder[0] (cap_idx=0).
    two = ["a", "b"]
    assert {ladder.initial_rung_for_tier(two, t)
            for t in ("quick", "standard", "thorough")} == {"a"}
    # len==3 -> quick=ladder[0]; standard and thorough both collapse onto ladder[1].
    three = ["a", "b", "c"]
    assert ladder.initial_rung_for_tier(three, "quick") == "a"
    assert ladder.initial_rung_for_tier(three, "standard") == "b"
    assert ladder.initial_rung_for_tier(three, "thorough") == "b"


def test_initial_rung_for_tier_unknown_tier_is_standard():
    lad = ["w", "x", "y", "z"]
    assert ladder.initial_rung_for_tier(lad, None) == ladder.initial_rung_for_tier(lad, "standard")
    assert ladder.initial_rung_for_tier(lad, "bogus") == ladder.initial_rung_for_tier(lad, "standard")


def test_initial_rung_for_tier_empty():
    assert ladder.initial_rung_for_tier([], "quick") == ""
    assert ladder.initial_rung_for_tier([], "thorough") == ""


def test_initial_rung_delegates_to_thorough():
    # The kept-for-compat initial_rung is exactly the 'thorough' rung.
    for lad in (["a"], ["a", "b"], ["a", "b", "c"], ["a", "b", "c", "d"]):
        assert ladder.initial_rung(lad) == ladder.initial_rung_for_tier(lad, "thorough")


def test_is_retriable():
    assert ladder.is_retriable("gate_failed") is True
    assert ladder.is_retriable("timeout") is True
    assert ladder.is_retriable("permission_denied") is False
    assert ladder.is_retriable(None) is False
