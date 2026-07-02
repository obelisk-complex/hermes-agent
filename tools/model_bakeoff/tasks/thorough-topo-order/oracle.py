import random

import pytest

from solution import topo_order


def _valid(deps, order):
    nodes = set(deps)
    for d in deps.values():
        nodes.update(d)
    if sorted(order) != sorted(nodes):
        return False
    pos = {n: i for i, n in enumerate(order)}
    for node, prereqs in deps.items():
        for p in prereqs:
            if pos[p] >= pos[node]:
                return False
    return True


@pytest.mark.parametrize("deps,e", [
    ({"b": ["a"], "c": ["a", "b"], "a": []}, ["a", "b", "c"]),
    ({}, []),
    ({"x": []}, ["x"]),
    ({"b": ["a"]}, ["a", "b"]),                 # 'a' only appears as a dependency
])
def test_exact(deps, e):
    assert topo_order(deps) == e


@pytest.mark.parametrize("deps", [
    {"a": ["b"], "b": ["a"]},
    {"a": ["a"]},
    {"a": ["b"], "b": ["c"], "c": ["a"]},
])
def test_cycle(deps):
    with pytest.raises(ValueError):
        topo_order(deps)


def test_seeded_random():
    rng = random.Random(12345)
    for _ in range(200):
        n = rng.randint(1, 7)
        names = [chr(ord("a") + i) for i in range(n)]
        deps = {name: [] for name in names}
        for i in range(n):
            for j in range(i):
                if rng.random() < 0.4:
                    deps[names[i]].append(names[j])  # only edges from later to earlier => acyclic
        order = topo_order(deps)
        assert _valid(deps, order)


def test_determinism():
    deps = {"d": ["a"], "c": ["a"], "b": ["a"], "a": []}
    assert topo_order(deps) == topo_order(deps)
    assert topo_order(deps)[0] == "a"
    assert topo_order(deps)[1:] == ["b", "c", "d"]
