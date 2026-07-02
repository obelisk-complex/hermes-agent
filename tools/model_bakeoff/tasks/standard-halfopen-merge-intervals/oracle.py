import random

import pytest

from solution import merge_halfopen


def _brute(iv):
    out = []
    for s, e in sorted(iv):
        if out and s < out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out


@pytest.mark.parametrize("iv,e", [
    ([], []),
    ([[1, 4], [4, 5]], [[1, 4], [4, 5]]),
    ([[1, 3], [2, 6], [8, 10]], [[1, 6], [8, 10]]),
    ([[1, 10], [2, 3]], [[1, 10]]),
    ([[5, 6], [1, 3]], [[1, 3], [5, 6]]),
])
def test_examples(iv, e):
    assert merge_halfopen([list(x) for x in iv]) == e


def test_seeded_random():
    rng = random.Random(999)
    for _ in range(200):
        iv = []
        for _ in range(rng.randint(0, 8)):
            a = rng.randint(0, 10)
            iv.append([a, a + rng.randint(1, 5)])
        assert merge_halfopen([list(x) for x in iv]) == _brute(iv)
