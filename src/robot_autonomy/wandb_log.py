"""Weights & Biases logging (extra `[wandb]`). W&B is a copy of the local ledger, never the source of truth.

`payload` is pure and never imports wandb; only `push` does, inside the function.
"""

from __future__ import annotations

import json
from dataclasses import asdict

from .agent import AgentConfig, AutonomyAgent
from .metrics import causes, daily


def payload(root, *, config: AgentConfig, now, hours=None) -> dict:
    """{"run_id": f"autonomy-{end}", "summary": {kpi fields}, "steps": daily(...),
    "tables": {"causes": [...], "backlog": [...], "blocks": [...]}}"""
    now = float(now)
    agent = AutonomyAgent(root, config, hours=hours)
    hours_table = agent.hours()
    all_episodes = agent.episodes()
    blocks = agent.ledger.blocks()
    kpi = agent._kpi(all_episodes, hours_table, blocks, now)
    summary = asdict(kpi)
    summary["readings"] = {reading.name: {"episodes": reading.episodes, "mtbi_h": reading.mtbi_h}
                           for reading in kpi.readings}
    fit = agent.growth(all_episodes, hours_table)
    summary["growth"] = None if fit is None else asdict(fit)
    steps = daily(all_episodes, hours_table, window_days=config.window_days, count_levels=config.count_levels,
                  tz=config.tz)
    cause_rows = []
    for row in causes(all_episodes, now=now):
        cause_row = asdict(row)
        cause_row["fixes"] = list(row.fixes)
        cause_rows.append(cause_row)
    return {"run_id": f"autonomy-{kpi.end}", "summary": summary, "steps": steps,
            "tables": {"causes": cause_rows, "backlog": [item.to_dict() for item in agent.backlog.items()],
                       "blocks": [block.to_dict() for block in blocks]}}


def _table(wandb, rows: list[dict]):
    columns = sorted({key for row in rows for key in row})
    data = [[_cell(row.get(column)) for column in columns] for row in rows]
    return wandb.Table(columns=columns, data=data)


def _cell(value):
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, sort_keys=True)
    return value


def push(root, *, config: AgentConfig, now, project, entity=None, hours=None) -> dict:
    """Log the payload to W&B. Idempotent per run id: the run is resumed and only days not yet logged are added."""
    import wandb

    data = payload(root, config=config, now=now, hours=hours)
    run = wandb.init(project=project, entity=entity, id=data["run_id"], name=data["run_id"], resume="allow",
                     job_type="robot-autonomy")
    try:
        last_day = run.summary.get("last_logged_day") if run.summary is not None else None
        logged = 0
        for day_index, step in enumerate(data["steps"]):
            if last_day is not None and step["day"] <= last_day:
                continue
            metrics = {key: value for key, value in step.items() if key != "day" and value is not None}
            metrics["day_index"] = day_index
            run.log(metrics, commit=True)
            last_day = step["day"]
            logged += 1
        flat = {}
        for key, value in data["summary"].items():
            if isinstance(value, dict):
                for inner_key, inner_value in value.items():
                    flat[f"{key}.{inner_key}"] = _cell(inner_value)
            else:
                flat[key] = value
        run.summary.update(flat)
        run.summary["last_logged_day"] = last_day
        for name, rows in data["tables"].items():
            if rows:
                run.log({f"table/{name}": _table(wandb, rows)}, commit=False)
    finally:
        run.finish()
    return {"run_id": data["run_id"], "steps_logged": logged, "project": project, "entity": entity}
