"""wandb_log.payload: pure, no wandb import (SPEC "wandb_log.py")."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

from helpers import make_intervention, t_of
from robot_autonomy.backlog import Backlog
from robot_autonomy.ledger import Ledger

PAYLOAD_SCRIPT = """
import json, sys
from robot_autonomy.agent import AgentConfig
from robot_autonomy import wandb_log
root, now = sys.argv[1], float(sys.argv[2])
hours = {("arm-1", f"2026-11-{day:02d}"): 10.0 for day in range(1, 18)}
result = wandb_log.payload(root, config=AgentConfig(tz="UTC"), now=now, hours=hours)
print(json.dumps({"payload": result, "wandb_imported": "wandb" in sys.modules}, default=str))
"""


def test_payload_is_pure_and_shaped(autonomy_root, fake_heavy_modules):
    ledger = Ledger(autonomy_root)
    for hour in (1, 2, 3):
        ledger.record(make_intervention(f"ask-{hour}", t_of("2026-11-16", hour), avoidable="yes", fix="powered hub"))
    Backlog(autonomy_root).propose(title="Add a powered hub", causes=("power_or_replug",), owner="dev agent",
                                   proposed_by="Robot Autonomy Agent", now=t_of("2026-11-17"))
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(fake_heavy_modules), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    completed = subprocess.run(
        [sys.executable, "-c", PAYLOAD_SCRIPT, str(autonomy_root), str(t_of("2026-11-18", 6.0))],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    output = json.loads(completed.stdout.strip().splitlines()[-1])
    assert output["wandb_imported"] is False
    payload = output["payload"]
    assert re.fullmatch(r"autonomy-\d{4}-\d{2}-\d{2}", payload["run_id"])
    assert isinstance(payload["summary"], dict) and payload["summary"]
    assert isinstance(payload["steps"], list) and payload["steps"]
    assert {"day", "working_h", "episodes", "mtbi_h", "level"} <= set(payload["steps"][0])
    assert {"causes", "backlog", "blocks"} <= set(payload["tables"])
    assert "power_or_replug" in json.dumps(payload["tables"]["causes"])
    assert "Add a powered hub" in json.dumps(payload["tables"]["backlog"])
