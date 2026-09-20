import tiers


def test_tiers_order():
    assert tiers.TIERS == ("quick", "standard", "thorough")


def test_quick_is_lint_only():
    assert tiers.kinds_for_tier("quick") == ("lint",)


def test_standard_is_lint_test():
    assert tiers.kinds_for_tier("standard") == ("lint", "test")


def test_thorough_is_all_kinds():
    assert tiers.kinds_for_tier("thorough") == ("lint", "test", "typecheck", "build")


def test_unknown_tier_falls_back_to_standard():
    assert tiers.kinds_for_tier("nonsense") == tiers.kinds_for_tier("standard")


def test_normalise_tier():
    assert tiers.normalise_tier("  THOROUGH ") == "thorough"
    assert tiers.normalise_tier(None) == "standard"
    assert tiers.normalise_tier("bogus") == "standard"
