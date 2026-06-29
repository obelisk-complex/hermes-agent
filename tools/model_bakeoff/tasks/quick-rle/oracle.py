import pytest

from solution import encode


@pytest.mark.parametrize("s,expected", [
    ("", ""),
    ("a", "a1"),
    ("aaabb", "a3b2"),
    ("abc", "a1b1c1"),
    ("aaaa", "a4"),
    ("xxyyyz", "x2y3z1"),
    ("aAaA", "a1A1a1A1"),
])
def test_encode(s, expected):
    assert encode(s) == expected
