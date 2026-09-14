"""Skills preloaded on every session, regardless of any --skills flag."""

from typing import Any, Mapping

# Upstream documents this key (optional-skills/creative/kanban-video-orchestrator
# writes it) but no upstream code reads it; `always` is the fork's original name
# and is already live in user configs. Both are accepted so neither an upstream
# skill's setup script nor an existing fork config silently stops preloading.
_ALWAYS_KEYS = ("always_load", "always")


def resolve_always_skills(cfg: Mapping[str, Any]) -> list[str]:
    """Skill names to preload on every session, in config order, deduped."""
    skills_cfg = cfg.get("skills") or {}
    names: list[str] = []
    for key in _ALWAYS_KEYS:
        value = skills_cfg.get(key) or []
        # A YAML scalar (`always: my-skill`) would otherwise splat into characters.
        if isinstance(value, str):
            value = [value]
        names.extend(str(s) for s in value)
    return list(dict.fromkeys(names))
