import random

import pytest

from solution import parse_flags


@pytest.mark.parametrize("argv,defaults,e", [
    (["--host", "prod", "--verbose"], {"host": "local", "verbose": False},
     {"host": "prod", "verbose": True}),
    ([], {"a": 1}, {"a": 1}),
    (["--x"], {}, {"x": True}),
    (["--x", "--y", "z"], {}, {"x": True, "y": "z"}),
    (["stray", "--k", "v"], {}, {"k": "v"}),
    (["--k", "a", "--k", "b"], {}, {"k": "b"}),
])
def test_examples(argv, defaults, e):
    assert parse_flags(argv, defaults) == e


def test_no_mutation():
    d = {"host": "local"}
    parse_flags(["--host", "prod"], d)
    assert d == {"host": "local"}


def test_seeded_random():
    rng = random.Random(7)
    keys = ["a", "b", "c"]
    for _ in range(200):
        argv, model = [], {}
        for _ in range(rng.randint(0, 6)):
            k = rng.choice(keys)
            argv.append("--" + k)
            if rng.random() < 0.5:
                v = str(rng.randint(0, 9))
                argv.append(v)
                model[k] = v
            else:
                model[k] = True
        assert parse_flags(argv, {}) == model
