import pytest

from solution import is_balanced


@pytest.mark.parametrize("s,ok", [
    ("", True),
    ("()", True),
    ("([{}])", True),
    ("(]", False),
    ("([)]", False),
    ("(((", False),
    ("a(b)c[d]", True),
    ("}{", False),
    ("{[()()]}", True),
    ("(", False),
])
def test_is_balanced(s, ok):
    assert is_balanced(s) is ok
