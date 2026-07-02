import random

import pytest

from solution import count_overlapping


def _brute(s, sub):
    if not sub:
        return 0
    return sum(1 for i in range(len(s) - len(sub) + 1) if s[i:i + len(sub)] == sub)


@pytest.mark.parametrize("s,sub,e", [
    ("aaaa", "aa", 3), ("abcabc", "abc", 2), ("aaa", "a", 3),
    ("", "a", 0), ("abc", "", 0), ("mississippi", "issi", 2),
])
def test_examples(s, sub, e):
    assert count_overlapping(s, sub) == e


def test_seeded_random():
    rng = random.Random(12345)
    for _ in range(200):
        s = "".join(rng.choice("ab") for _ in range(rng.randint(0, 12)))
        sub = "".join(rng.choice("ab") for _ in range(rng.randint(1, 3)))
        assert count_overlapping(s, sub) == _brute(s, sub)
