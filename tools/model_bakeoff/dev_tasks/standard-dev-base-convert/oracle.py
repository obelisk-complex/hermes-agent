import random

import pytest

from solution import to_base

_DIGITS = "0123456789abcdef"


def _brute(n, b):
    if n == 0:
        return "0"
    out = []
    while n:
        n, r = divmod(n, b)
        out.append(_DIGITS[r])
    return "".join(reversed(out))


@pytest.mark.parametrize("n,b,e", [
    (0, 2, "0"), (0, 16, "0"), (10, 2, "1010"), (255, 16, "ff"),
    (255, 2, "11111111"), (100, 10, "100"), (7, 8, "7"), (8, 8, "10"),
    (26, 16, "1a"), (1, 2, "1"),
])
def test_examples(n, b, e):
    assert to_base(n, b) == e


@pytest.mark.parametrize("bad_b", [-1, 0, 1, 17, 100])
def test_bad_base_raises(bad_b):
    with pytest.raises(ValueError):
        to_base(10, bad_b)


def test_seeded_random():
    rng = random.Random(2718281)
    for _ in range(300):
        n = rng.randint(0, 5_000_000)
        b = rng.randint(2, 16)
        assert to_base(n, b) == _brute(n, b)
