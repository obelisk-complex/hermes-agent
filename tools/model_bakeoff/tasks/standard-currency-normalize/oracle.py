import random

import pytest

from solution import normalize_number


@pytest.mark.parametrize("s,e", [
    ("1,234.50", 1234.5), ("$1,000", 1000.0), ("12.5%", 0.125),
    ("(1,000)", -1000.0), ("-42", -42.0), ("  £3.14  ", 3.14),
    ("0", 0.0), ("1000000", 1000000.0), ("(50%)", -0.5),
])
def test_examples(s, e):
    assert normalize_number(s) == pytest.approx(e)


@pytest.mark.parametrize("bad", ["", "   ", "abc", "$", "1.2.3", "1,2,,3x", "()"])
def test_malformed(bad):
    with pytest.raises(ValueError):
        normalize_number(bad)


def test_seeded_random():
    rng = random.Random(999)
    for _ in range(200):
        whole = rng.randint(0, 9999)
        frac = rng.randint(0, 99)
        raw = f"{whole}.{frac:02d}"
        expected = float(raw)
        s = raw
        if rng.random() < 0.5:
            s = rng.choice(["$", "£", "€"]) + s
        assert normalize_number(s) == pytest.approx(expected)
