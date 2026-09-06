from hermes_cli.plugins import VALID_HOOKS


def test_kanban_lifecycle_hooks_registered():
    for name in ("pre_kanban_spawn", "fork_kanban_task_blocked", "pre_kanban_complete"):
        assert name in VALID_HOOKS, f"{name} missing from VALID_HOOKS"
