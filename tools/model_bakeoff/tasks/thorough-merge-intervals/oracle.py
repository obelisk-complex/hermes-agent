import pytest

from solution import merge


@pytest.mark.parametrize("intervals,expected", [
    ([], []),
    ([[1, 3]], [[1, 3]]),
    ([[1, 3], [2, 6], [8, 10], [15, 18]], [[1, 6], [8, 10], [15, 18]]),
    ([[1, 4], [4, 5]], [[1, 5]]),
    ([[5, 6], [1, 3]], [[1, 3], [5, 6]]),
    ([[1, 10], [2, 3], [4, 5]], [[1, 10]]),
    ([[1, 2], [3, 4]], [[1, 2], [3, 4]]),
])
def test_merge(intervals, expected):
    assert merge(intervals) == expected
