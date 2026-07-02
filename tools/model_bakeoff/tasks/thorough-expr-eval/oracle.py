import pytest

from solution import evaluate


@pytest.mark.parametrize("x,e", [
    ("2+3*4", 14), ("(2+3)*4", 20), ("-(2+3)", -5), ("2*-3", -6),
    ("-7/2", -3), ("7/-2", -3), ("-8/2", -4), ("100/3", 33),
    ("1+2-3+4", 4), ("2*3*4", 24), ("((1))", 1), (" 2 + 2 ", 4),
])
def test_values(x, e):
    assert evaluate(x) == e


@pytest.mark.parametrize("bad", ["", "(", "1+", "2**3", "1/0", "3 4", "+", "()"])
def test_malformed(bad):
    with pytest.raises(ValueError):
        evaluate(bad)
