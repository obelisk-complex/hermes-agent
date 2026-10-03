"""Fork: legacy ``skills.always`` / ``skills.always_load`` still pin skills via ``skills.auto_load``.

``always`` is the fork's original key and is live in user configs; ``always_load`` is what the
kanban-video-orchestrator skill writes into worker profiles. Both fold into upstream's auto_load
path so neither silently stops loading.
"""

from __future__ import annotations

import pytest


def _write_skill(root, name, body):
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Test.\n---\n\n{body}\n")


@pytest.mark.parametrize(
    "skills_cfg,expected",
    [
        ({"always": ["fork-a", "fork-b"]}, ["fork-a", "fork-b"]),
        ({"always_load": ["up-a"]}, ["up-a"]),
        ({"auto_load": ["x"], "always_load": ["y", "x"], "always": ["z", "y"]}, ["x", "y", "z"]),
        ({"always": "solo-skill"}, ["solo-skill"]),  # YAML scalar must not splat into characters
        ({"always": None, "always_load": None}, []),
        ({}, []),
    ],
)
def test_legacy_keys_fold_into_auto_load(skills_cfg, expected):
    from agent.skill_commands import resolve_auto_load_skills

    assert resolve_auto_load_skills({"skills": skills_cfg}) == expected


def test_config_with_only_legacy_key_auto_loads_skill(tmp_path):
    """End to end through upstream's profile-scoped loader: config sets only ``skills.always``."""
    from agent.skill_commands import build_auto_load_prompt

    home = tmp_path / "profile-home"
    _write_skill(home / "skills", "legacy-pinned", "LEGACY PINNED CONTENT")
    (home / "config.yaml").write_text("skills:\n  always: [legacy-pinned]\n", encoding="utf-8")
    prompt, loaded, missing = build_auto_load_prompt(task_id="s1", home_override=home)
    assert loaded == ["legacy-pinned"] and missing == []
    assert "LEGACY PINNED CONTENT" in prompt
