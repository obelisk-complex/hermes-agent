from __future__ import annotations

import importlib
import os
import sys
from unittest.mock import MagicMock, patch

import pytest


def _make_real_cli(**kwargs):
    clean_config = {
        "model": {
            "default": "anthropic/claude-opus-4.6",
            "base_url": "https://openrouter.ai/api/v1",
            "provider": "auto",
        },
        "display": {"compact": False, "tool_progress": "all"},
        "agent": {},
        "terminal": {"env_type": "local"},
    }
    clean_env = {"LLM_MODEL": "", "HERMES_MAX_ITERATIONS": ""}
    prompt_toolkit_stubs = {
        "prompt_toolkit": MagicMock(),
        "prompt_toolkit.history": MagicMock(),
        "prompt_toolkit.styles": MagicMock(),
        "prompt_toolkit.patch_stdout": MagicMock(),
        "prompt_toolkit.application": MagicMock(),
        "prompt_toolkit.layout": MagicMock(),
        "prompt_toolkit.layout.processors": MagicMock(),
        "prompt_toolkit.filters": MagicMock(),
        "prompt_toolkit.layout.dimension": MagicMock(),
        "prompt_toolkit.layout.menus": MagicMock(),
        "prompt_toolkit.widgets": MagicMock(),
        "prompt_toolkit.key_binding": MagicMock(),
        "prompt_toolkit.completion": MagicMock(),
        "prompt_toolkit.formatted_text": MagicMock(),
    }
    with patch.dict(sys.modules, prompt_toolkit_stubs), patch.dict(
        "os.environ", clean_env, clear=False
    ):
        import cli as cli_mod

        cli_mod = importlib.reload(cli_mod)
        with patch.object(cli_mod, "get_tool_definitions", return_value=[]), patch.dict(
            cli_mod.__dict__, {"CLI_CONFIG": clean_config}
        ):
            return cli_mod.HermesCLI(**kwargs)


class _DummyCLI:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.session_id = "session-123"
        self.system_prompt = "base prompt"
        self.preloaded_skills = []

    def show_banner(self):
        return None

    def show_tools(self):
        return None

    def show_toolsets(self):
        return None

    def run(self):
        return None


def test_main_applies_preloaded_skills_to_system_prompt(monkeypatch):
    import cli as cli_mod

    created = {}

    def fake_cli(**kwargs):
        created["cli"] = _DummyCLI(**kwargs)
        return created["cli"]

    monkeypatch.setattr(cli_mod, "HermesCLI", fake_cli)
    monkeypatch.setattr(
        cli_mod,
        "build_preloaded_skills_prompt",
        lambda skills, task_id=None: ("skill prompt", ["hermes-agent-dev", "github-auth"], []),
    )

    with pytest.raises(SystemExit):
        cli_mod.main(skills="hermes-agent-dev,github-auth", list_tools=True)

    cli_obj = created["cli"]
    assert cli_obj.system_prompt == "base prompt\n\nskill prompt"
    assert cli_obj.preloaded_skills == ["hermes-agent-dev", "github-auth"]


def test_main_raises_for_unknown_preloaded_skill(monkeypatch):
    import cli as cli_mod

    monkeypatch.setattr(cli_mod, "HermesCLI", lambda **kwargs: _DummyCLI(**kwargs))
    monkeypatch.setattr(
        cli_mod,
        "build_preloaded_skills_prompt",
        lambda skills, task_id=None: ("", [], ["missing-skill"]),
    )

    with pytest.raises(ValueError, match=r"Unknown skill\(s\): missing-skill"):
        cli_mod.main(skills="missing-skill", list_tools=True)


def test_show_banner_does_not_print_skills():
    """show_banner() no longer prints the activated skills line — it moved to run()."""
    cli_obj = _make_real_cli(compact=False)
    cli_obj.preloaded_skills = ["hermes-agent-dev", "github-auth"]
    cli_obj.console = MagicMock()

    with patch("cli.build_welcome_banner") as mock_banner, patch(
        "shutil.get_terminal_size", return_value=os.terminal_size((120, 40))
    ):
        cli_obj.show_banner()

    print_calls = [
        call.args[0]
        for call in cli_obj.console.print.call_args_list
        if call.args and isinstance(call.args[0], str)
    ]
    startup_lines = [line for line in print_calls if "Activated skills:" in line]
    assert len(startup_lines) == 0
    assert mock_banner.call_count == 1


def test_main_merges_skills_always_from_config(monkeypatch):
    """skills.always from config is merged into parsed_skills before
    build_preloaded_skills_prompt is called."""
    import cli as cli_mod

    created = {}

    def fake_cli(**kwargs):
        created["cli"] = _DummyCLI(**kwargs)
        return created["cli"]

    monkeypatch.setattr(cli_mod, "HermesCLI", fake_cli)

    captured_skills = []

    def fake_build(skills, task_id=None):
        captured_skills.append(list(skills))
        return ("skill prompt", skills, [])

    monkeypatch.setattr(cli_mod, "build_preloaded_skills_prompt", fake_build)

    # Patch CLI_CONFIG to include skills.always
    patched_config = {
        "model": {"default": "test", "provider": "auto"},
        "display": {"compact": False, "tool_progress": "all"},
        "agent": {},
        "terminal": {"env_type": "local"},
        "skills": {"always": ["self-checking-harness"]},
    }

    with patch.dict(cli_mod.__dict__, {"CLI_CONFIG": patched_config}):
        with pytest.raises(SystemExit):
            cli_mod.main(list_tools=True)

    assert len(captured_skills) == 1
    assert captured_skills[0] == ["self-checking-harness"]


def test_main_merges_skills_always_with_cli_flag(monkeypatch):
    """skills.always from config is merged with --skills flag (config first, deduped)."""
    import cli as cli_mod

    created = {}

    def fake_cli(**kwargs):
        created["cli"] = _DummyCLI(**kwargs)
        return created["cli"]

    monkeypatch.setattr(cli_mod, "HermesCLI", fake_cli)

    captured_skills = []

    def fake_build(skills, task_id=None):
        captured_skills.append(list(skills))
        return ("skill prompt", skills, [])

    monkeypatch.setattr(cli_mod, "build_preloaded_skills_prompt", fake_build)

    patched_config = {
        "model": {"default": "test", "provider": "auto"},
        "display": {"compact": False, "tool_progress": "all"},
        "agent": {},
        "terminal": {"env_type": "local"},
        "skills": {"always": ["self-checking-harness"]},
    }

    with patch.dict(cli_mod.__dict__, {"CLI_CONFIG": patched_config}):
        with pytest.raises(SystemExit):
            # --skills adds github-auth; config always has self-checking-harness
            cli_mod.main(skills="github-auth", list_tools=True)

    assert len(captured_skills) == 1
    # Config skills come first, then CLI skills
    assert captured_skills[0] == ["self-checking-harness", "github-auth"]


def test_main_skills_always_empty_does_nothing(monkeypatch):
    """Empty skills.always does not affect the skills list."""
    import cli as cli_mod

    created = {}

    def fake_cli(**kwargs):
        created["cli"] = _DummyCLI(**kwargs)
        return created["cli"]

    monkeypatch.setattr(cli_mod, "HermesCLI", fake_cli)

    captured_skills = []

    def fake_build(skills, task_id=None):
        captured_skills.append(list(skills))
        return ("skill prompt", skills, [])

    monkeypatch.setattr(cli_mod, "build_preloaded_skills_prompt", fake_build)

    patched_config = {
        "model": {"default": "test", "provider": "auto"},
        "display": {"compact": False, "tool_progress": "all"},
        "agent": {},
        "terminal": {"env_type": "local"},
        "skills": {"always": []},
    }

    with patch.dict(cli_mod.__dict__, {"CLI_CONFIG": patched_config}):
        with pytest.raises(SystemExit):
            cli_mod.main(skills="github-auth", list_tools=True)

    assert len(captured_skills) == 1
    # Only the CLI flag skill, no config merge
    assert captured_skills[0] == ["github-auth"]
