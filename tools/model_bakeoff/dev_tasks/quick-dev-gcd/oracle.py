import math
import random

import pytest

from solution import gcd


def _brute(a, b):
    return math.gcd(a, b)


@pytest.mark.parametrize("a,b,e", [
    (12, 18, 6), (18, 12, 6), (0, 5, 5), (5, 0, 5), (0, 0, 0),
    (100, 10, 10), (17, 5, 1), (81, 27, 27), (1, 1, 1), (0, 1, 1),
])
def test_examples(a, b, e):
    assert gcd(a, b) == e


def test_seeded_random():
    rng = random.Random(20260702)
    for _ in range(300):
        a = rng.randint(0, 10_000)
        b = rng.randint(0, 10_000)
        assert gcd(a, b) == _brute(a, b)
