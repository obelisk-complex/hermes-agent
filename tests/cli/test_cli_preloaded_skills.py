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


def _real_finalize(cli_obj):
    """Call the real HermesCLI.finalize_preloaded_skills on a dummy object."""
    return _REAL_FINALIZE(cli_obj)


def _capture_real_finalize():
    import cli as cli_mod
    return cli_mod.HermesCLI.__dict__["finalize_preloaded_skills"]


_REAL_FINALIZE = _capture_real_finalize()


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
    # The preload now runs in a background thread and is folded in at agent
    # init via finalize_preloaded_skills() (startup-latency change). Drive
    # the finalize explicitly — the same call _init_agent makes.
    _real_finalize(cli_obj)
    assert cli_obj.system_prompt == "base prompt\n\nskill prompt"
    assert cli_obj.preloaded_skills == ["hermes-agent-dev", "github-auth"]


def test_main_raises_for_unknown_preloaded_skill(monkeypatch):
    import cli as cli_mod

    created = {}

    def fake_cli(**kwargs):
        created["cli"] = _DummyCLI(**kwargs)
        return created["cli"]

    monkeypatch.setattr(cli_mod, "HermesCLI", fake_cli)
    monkeypatch.setattr(
        cli_mod,
        "build_preloaded_skills_prompt",
        lambda skills, task_id=None: ("", [], ["missing-skill"]),
    )

    with pytest.raises(SystemExit):
        cli_mod.main(skills="missing-skill", list_tools=True)

    # The all-skills-unknown hard failure now surfaces when the preload is
    # finalized (agent init), preserving the fail-loud contract.
    with pytest.raises(ValueError, match=r"Unknown skill\(s\): missing-skill"):
        _real_finalize(created["cli"])


def test_show_banner_does_not_print_skills():
    """show_banner() no longer prints the activated skills line — it moved to run()."""
    cli_obj = _make_real_cli(compact=False)
    cli_obj.preloaded_skills = ["hermes-agent-dev", "github-auth"]
    cli_obj.console = MagicMock()

    with patch("hermes_cli.banner.build_welcome_banner") as mock_banner, patch(
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


# ── fork: skills.always ──────────────────────────────────────────────────────
# Config-declared skills preload on every session with no --skills flag. The
# merge happens in _build_cli_from_args (upstream moved it out of main()), so
# these drive main() and observe what reaches build_preloaded_skills_prompt.
# The preload runs on a background thread now, so finalize is driven explicitly
# — the same call _init_agent makes — before asserting.

def _capture_preloaded_skills(monkeypatch, cli_mod, config_skills, **main_kwargs):
    """Run main() with ``skills`` config patched in; return the requested lists."""
    created = {}
    monkeypatch.setattr(
        cli_mod, "HermesCLI", lambda **kw: created.setdefault("cli", _DummyCLI(**kw))
    )
    captured: list[list[str]] = []

    def fake_build(skills, task_id=None):
        captured.append(list(skills))
        return ("skill prompt", list(skills), [])

    monkeypatch.setattr(cli_mod, "build_preloaded_skills_prompt", fake_build)
    patched_config = {
        "model": {"default": "test", "provider": "auto"},
        "display": {"compact": False, "tool_progress": "all"},
        "agent": {},
        "terminal": {"env_type": "local"},
        "skills": config_skills,
    }
    with patch.dict(cli_mod.__dict__, {"CLI_CONFIG": patched_config}):
        with pytest.raises(SystemExit):
            cli_mod.main(list_tools=True, **main_kwargs)
    if "cli" in created:
        _real_finalize(created["cli"])
    return captured


def test_main_merges_skills_always_from_config(monkeypatch):
    """skills.always preloads with no --skills flag given."""
    import cli as cli_mod

    captured = _capture_preloaded_skills(
        monkeypatch, cli_mod, {"always": ["self-checking-harness"]}
    )
    assert captured == [["self-checking-harness"]]


def test_main_merges_skills_always_with_cli_flag(monkeypatch):
    """Config skills come first, --skills adds on top, duplicates collapse."""
    import cli as cli_mod

    captured = _capture_preloaded_skills(
        monkeypatch, cli_mod, {"always": ["self-checking-harness"]},
        skills="github-auth,self-checking-harness",
    )
    assert len(captured) == 1
    assert captured[0] == ["self-checking-harness", "github-auth"]


def test_main_skills_always_empty_does_nothing(monkeypatch):
    """An empty (or absent) skills.always must not trigger a preload at all."""
    import cli as cli_mod

    assert _capture_preloaded_skills(monkeypatch, cli_mod, {"always": []}) == []
    assert _capture_preloaded_skills(monkeypatch, cli_mod, {}) == []


def test_skills_always_load_alias_also_preloads(monkeypatch):
    """upstream's documented key name is accepted alongside the fork's."""
    import cli as cli_mod

    captured = _capture_preloaded_skills(
        monkeypatch, cli_mod, {"always_load": ["self-checking-harness"]}
    )
    assert captured == [["self-checking-harness"]]
