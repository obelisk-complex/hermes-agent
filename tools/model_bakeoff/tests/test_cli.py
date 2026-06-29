"""cli commands (SPEC §10). Offline: validate-oracles + estimate run for real;
run is driven end-to-end through a fake transport and writes all artefacts."""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

from tools.model_bakeoff import cli, corpus, registry


def test_validate_oracles_command_passes():
    assert cli.cmd_validate_oracles(SimpleNamespace(timeout=30)) == 0


def test_estimate_subscription_is_free_metered_is_not():
    tasks = corpus.load()
    sub = [m for m in registry.ROSTER if m.cost_model == "subscription"][:1]
    opus = [m for m in registry.ROSTER if m.key == "claude-opus-4-8"]
    _, sub_total, _ = cli.estimate(sub, tasks, repeats=3)
    _, opus_total, _ = cli.estimate(opus, tasks, repeats=3)
    assert sub_total == 0.0
    assert opus_total > 0.0


def test_estimate_flags_unpriced_metered_models():
    # metered but priceless => must be flagged, never silently counted as $0
    tasks = corpus.load()
    metered_unpriced = [m for m in registry.ROSTER
                        if m.is_metered and not (m.price_in_per_m and m.price_out_per_m)]
    assert metered_unpriced, "expected at least one unpriced metered model in the roster"
    _, _, unpriced = cli.estimate(metered_unpriced, tasks, repeats=1)
    assert set(unpriced) == {m.key for m in metered_unpriced}


def test_estimate_command_returns_2_when_over_budget():
    args = SimpleNamespace(models="claude-opus-4-8", repeats=3, budget=0.0)
    assert cli.cmd_estimate(args) == 2


def test_run_writes_all_artefacts(tmp_path):
    async def fake(url, headers, json_body, timeout):
        return 200, {"choices": [{"message": {"content": "```python\ndef f(x):\n    return x\n```"}}],
                     "usage": {"prompt_tokens": 10, "completion_tokens": 5}}, None

    # transport kwargs are named url/headers/json/timeout in client; bind by name
    async def fake_transport(url, headers, json, timeout):
        return await fake(url, headers, json, timeout)

    env = {"BAKEOFF_GATEWAY_URL": "https://x/v1", "BAKEOFF_GATEWAY_KEY": "k"}
    out = str(tmp_path / "run1")
    args = SimpleNamespace(models="deepseek-v4-flash,claude-opus-4-8",
                           repeats=1, budget=10.0, out=out)
    rc = cli.cmd_run(args, env=env, transport=fake_transport)
    assert rc == 0
    assert os.path.exists(os.path.join(out, "report.md"))
    assert os.path.exists(os.path.join(out, "ladder.yaml"))
    summary = json.load(open(os.path.join(out, "summary.json")))
    assert summary["ladder"][-1] == "claude-opus-4-8"  # ceiling pinned last
    assert os.listdir(os.path.join(out, "raw"))  # raw model outputs persisted
