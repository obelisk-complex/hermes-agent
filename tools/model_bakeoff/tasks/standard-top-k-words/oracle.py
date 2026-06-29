from solution import top_k


def test_basic_frequency_order():
    assert top_k("a a b b b c", 2) == ["b", "a"]


def test_ties_broken_alphabetically():
    assert top_k("banana apple apple banana cherry", 2) == ["apple", "banana"]


def test_case_insensitive():
    assert top_k("The the THE dog", 1) == ["the"]


def test_k_larger_than_vocabulary_returns_all():
    assert top_k("x y", 5) == ["x", "y"]


def test_empty_text_returns_empty():
    assert top_k("", 3) == []


def test_k_zero_returns_empty():
    assert top_k("a a b", 0) == []
