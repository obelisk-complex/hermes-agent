"""The self-check enforcer ships as a BUNDLED plugin and wires its hooks.

Guards the reference-design promise: a fresh clone loads the enforcer (and its
on_output gate) with zero hand-installation. Stdlib-only; runs standalone or
under pytest.
"""
import importlib.util
import os

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PLUGIN = os.path.join(_REPO, "plugins", "self-check-enforcer", "__init__.py")
_YAML = os.path.join(_REPO, "plugins", "self-check-enforcer", "plugin.yaml")


def _load():
    spec = importlib.util.spec_from_file_location("self_check_enforcer_bundled", _PLUGIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _RecordingContext:
    def __init__(self):
        self.registered = set()

    def register_hook(self, name, callback):
        self.registered.add(name)


def test_bundled_plugin_files_exist():
    assert os.path.isfile(_PLUGIN), f"bundled enforcer missing at {_PLUGIN}"
    assert os.path.isfile(_YAML), f"bundled manifest missing at {_YAML}"


def test_bundled_plugin_registers_on_output_and_eight_hooks():
    mod = _load()
    ctx = _RecordingContext()
    mod.register(ctx)
    assert "on_output" in ctx.registered, f"on_output not wired; got {sorted(ctx.registered)}"
    assert len(ctx.registered) >= 8, f"expected >=8 hooks, got {sorted(ctx.registered)}"


def test_manifest_declares_the_gate_hooks():
    with open(_YAML, encoding="utf-8") as f:
        manifest = f.read()
    for hook in ("on_output", "subagent_stop", "pre_tool_call"):
        assert hook in manifest, f"plugin.yaml does not declare {hook}"


def test_version_is_v371_or_later():
    with open(_PLUGIN, encoding="utf-8") as f:
        head = f.read(400)
    assert "v3.7.1" in head, "bundled plugin docstring should be v3.7.1+"


if __name__ == "__main__":
    import sys
    _tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    _p = _f = 0
    for _t in _tests:
        try:
            _t(); _p += 1; print(f"  ✓ {_t.__name__}")
        except AssertionError as e:
            _f += 1; print(f"  ✗ {_t.__name__} — {e}")
    print(f"\n=== {_p} passed, {_f} failed ===")
    sys.exit(1 if _f else 0)
