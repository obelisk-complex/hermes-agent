import random

import pytest

from solution import digit_root


def _brute(n):
    while n >= 10:
        n = sum(int(c) for c in str(n))
    return n


@pytest.mark.parametrize("n,e", [
    (0, 0), (9, 9), (10, 1), (99, 9), (123, 6), (9999, 9),
    (12345, 6), (100, 1), (18, 9), (19, 1),
])
def test_examples(n, e):
    assert digit_root(n) == e


def test_seeded_random():
    rng = random.Random(3141592)
    for _ in range(300):
        n = rng.randint(0, 10_000_000)
        assert digit_root(n) == _brute(n)
