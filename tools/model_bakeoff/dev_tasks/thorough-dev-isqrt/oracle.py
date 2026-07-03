import math
import random

import pytest

from solution import isqrt


def _brute(n):
    return math.isqrt(n)


@pytest.mark.parametrize("n,e", [
    (0, 0), (1, 1), (2, 1), (3, 1), (4, 2), (8, 2), (9, 3), (15, 3), (16, 4),
    # Large-n cases where a naive int(n ** 0.5) / int(math.sqrt(n)) loses float
    # precision and returns an off-by-one:
    (10**18, 10**9),
    ((10**8 + 1) ** 2 - 1, 10**8),
    (2 * 10**18, _brute(2 * 10**18)),
    (10**50, _brute(10**50)),
])
def test_examples(n, e):
    assert isqrt(n) == e


def test_negative_raises():
    with pytest.raises(ValueError):
        isqrt(-1)


def test_seeded_random_large():
    rng = random.Random(1618033)
    for _ in range(300):
        r = rng.randint(0, 10**15)
        # n uniformly in [r*r, (r+1)**2 - 1], so isqrt(n) must be exactly r.
        n = rng.randint(r * r, (r + 1) ** 2 - 1)
        assert isqrt(n) == r
