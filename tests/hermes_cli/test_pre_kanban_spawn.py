import hermes_cli.kanban_db as kdb


class _Claimed:
    def __init__(self):
        self.id = "t_spawn01"
        self.title = "demo"
        self.body = "b"
        self.assignee = "worker"
        self.model_override = None
        self.workspace_kind = "scratch"
        self.branch_name = None
        self.priority = 0
        self.skills = None
        self.consecutive_failures = 0


def test_invoke_kanban_hook_returns_results(monkeypatch):
    seen = {}

    def fake_invoke(name, **kwargs):
        seen["name"] = name
        seen["kwargs"] = kwargs
        return [{"model_override": "claude-opus-4-8"}]

    monkeypatch.setattr(
        "hermes_cli.plugins.invoke_hook", fake_invoke, raising=True
    )
    out = kdb._invoke_kanban_hook(
        "pre_kanban_spawn", task_id="t_spawn01", title="demo"
    )
    assert out == [{"model_override": "claude-opus-4-8"}]
    assert seen["name"] == "pre_kanban_spawn"
    assert seen["kwargs"]["task_id"] == "t_spawn01"


def test_invoke_kanban_hook_swallows_import_error(monkeypatch):
    # Guards the per-call local import in _invoke_kanban_hook: a partial
    # install where hermes_cli.plugins fails to import must degrade to []
    # rather than raising. This relies on the import being per-call (NOT
    # cached at module level); see the impl docstring.
    import builtins
    real_import = builtins.__import__

    def boom(name, *a, **k):
        if name == "hermes_cli.plugins":
            raise ImportError("simulated partial install")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", boom)
    # Must degrade to [] rather than raising.
    assert kdb._invoke_kanban_hook("pre_kanban_spawn", task_id="x") == []


def test_pre_kanban_spawn_override_applies_first_dict(monkeypatch):
    claimed = _Claimed()
    monkeypatch.setattr(
        kdb, "_invoke_kanban_hook",
        lambda name, **kw: [
            {"model_override": "claude-opus-4-8", "skills": ["qa"]},
            {"model_override": "ignored-second"},
        ],
    )
    kdb._apply_pre_kanban_spawn_override(claimed, board=None,
                                         workspace_path="/tmp/ws")
    assert claimed.model_override == "claude-opus-4-8"
    assert claimed.skills == ["qa"]


def test_pre_kanban_spawn_override_rejects_flag_injection(monkeypatch):
    # A returned override that would inject a CLI flag must be dropped, not
    # spliced into the worker argv (B5).
    claimed = _Claimed()
    claimed.model_override = "keep-me"
    claimed.skills = ["keep-skill"]
    monkeypatch.setattr(
        kdb, "_invoke_kanban_hook",
        lambda name, **kw: [
            {"model_override": "--accept-hooks",
             "skills": ["--accept-hooks", "ok", "a,b"]},
        ],
    )
    kdb._apply_pre_kanban_spawn_override(claimed, board=None,
                                         workspace_path="/tmp/ws")
    # model_override starts with '-' → rejected → existing value kept.
    assert claimed.model_override == "keep-me"
    # skills: only the clean name survives (flag + comma dropped).
    assert claimed.skills == ["ok"]


def test_pre_kanban_spawn_override_all_invalid_skips_directive(monkeypatch):
    claimed = _Claimed()
    claimed.model_override = "keep-me"
    monkeypatch.setattr(
        kdb, "_invoke_kanban_hook",
        lambda name, **kw: [
            {"model_override": "-bad"},                 # all invalid → skip
            {"model_override": "claude-opus-4-8"},      # next valid wins
        ],
    )
    kdb._apply_pre_kanban_spawn_override(claimed, board=None,
                                         workspace_path="/tmp/ws")
    assert claimed.model_override == "claude-opus-4-8"
