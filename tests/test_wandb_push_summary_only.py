"""wandb_log.push sends W&B summary statistics only (SPEC "wandb_log.py"; Zach, 2026-11-21: "to help limit W and B storage I think we
need to just be using it for summary statistics; for the media-rich things we should be using rerun!").

push against a stand-in `wandb` whose Table (and every media type) raises: steps and summary are scalars, the tables land as jsonl in
<root>/tables/<run id>/, and the summary names that folder. No network.
"""

from __future__ import annotations

import json
import sys
import types

from helpers import make_intervention, t_of
from robot_autonomy.agent import AgentConfig
from robot_autonomy.backlog import Backlog
from robot_autonomy.ledger import Ledger
from robot_autonomy import wandb_log


def refuse(*args, **kwargs):
    raise AssertionError("summary-only: a media or table object was built for W&B")


class Run:
    def __init__(self, kwargs):
        self.kwargs, self.logs, self.summary = kwargs, [], {}

    def log(self, record, commit=True):
        self.logs.append(record)

    def finish(self):
        pass


def test_push_sends_scalars_and_writes_the_tables_beside_the_ledger(autonomy_root, monkeypatch):
    ledger = Ledger(autonomy_root)
    for hour in (1, 2, 3):
        ledger.record(make_intervention(f"ask-{hour}", t_of("2026-11-16", hour), avoidable="yes", fix="powered hub"))
    Backlog(autonomy_root).propose(title="Add a powered hub", causes=("power_or_replug",), owner="dev agent",
                                   proposed_by="Robot Autonomy Agent", now=t_of("2026-11-17"))
    runs = []
    fake = types.SimpleNamespace(init=lambda **kwargs: runs.append(Run(kwargs)) or runs[-1],
                                 **{name: refuse for name in ("Table", "Image", "Video", "Html", "Plotly", "Histogram", "Artifact")})
    monkeypatch.setitem(sys.modules, "wandb", fake)
    hours = {("arm-1", f"2026-11-{day:02d}"): 10.0 for day in range(1, 18)}
    result = wandb_log.push(autonomy_root, config=AgentConfig(tz="UTC"), now=t_of("2026-11-18", 6.0), project="p", hours=hours)
    (run,) = runs
    assert result["steps_logged"] > 0
    assert all(isinstance(v, (int, float, str, bool, type(None))) for record in run.logs for v in record.values())
    assert all(isinstance(v, (int, float, str, bool, type(None))) for v in run.summary.values())
    folder = autonomy_root / "tables" / result["run_id"]
    assert run.summary["local_tables"] == str(folder)
    causes = [json.loads(line) for line in (folder / "causes.jsonl").read_text().splitlines()]
    assert causes and len(causes) == run.summary["table/causes_rows"]
