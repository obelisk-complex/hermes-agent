"""skills.always / skills.always_load alias resolution.

`always` is the fork's original key and is live in user configs; `always_load`
is the name upstream's docs and bundled skills use. Dropping either silently
stops preloading rather than erroring, so both paths are pinned here.
"""

import pytest

from hermes_cli.skills_always import resolve_always_skills


@pytest.mark.parametrize(
    "cfg,expected",
    [
        ({"skills": {"always_load": ["up-a"], "always": ["fork-a"]}}, ["up-a", "fork-a"]),
        ({"skills": {"always": ["fork-a", "fork-b"]}}, ["fork-a", "fork-b"]),
        ({"skills": {"always_load": ["up-a"]}}, ["up-a"]),
        ({"skills": {"always_load": ["x", "y"], "always": ["y", "z"]}}, ["x", "y", "z"]),
        ({"skills": {}}, []),
        ({}, []),
        ({"skills": {"always": None, "always_load": None}}, []),
    ],
)
def test_resolve_always_skills(cfg, expected):
    assert resolve_always_skills(cfg) == expected


def test_yaml_scalar_is_not_splatted_into_characters():
    assert resolve_always_skills({"skills": {"always": "solo-skill"}}) == ["solo-skill"]


def test_config_defaults_declares_both_keys():
    """Both must be in DEFAULT_CONFIG or `hermes config set` reports them unknown."""
    from hermes_cli.config_defaults import DEFAULT_CONFIG

    assert "always" in DEFAULT_CONFIG["skills"]
    assert "always_load" in DEFAULT_CONFIG["skills"]
