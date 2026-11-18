"""The AI Team directory's status card (`ai-team-status/1`): the KPI and the todo list, from code reading the files.

The kpi fields come from code reading the ledger, never from the chat's claim; only status and blocked_on may.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .backlog import OPEN, Backlog

if TYPE_CHECKING:   # pragma: no cover
    from .agent import AgentConfig
    from .metrics import Growth, Kpi

TEAM_SCHEMA = "ai-team-status/1"
STATUSES = ("working", "waiting_on_zach", "blocked", "idle")
WEEK_S = 7 * 86400.0


def _levels_phrase(count_levels) -> str:
    return " or ".join(level.replace("_", "-") for level in count_levels)


def status_card(kpi: "Kpi", backlog: Backlog, *, config: "AgentConfig", now: float, growth: "Growth | None" = None,
                claim: dict | None = None) -> dict:
    items = backlog.items()
    open_items = [item for item in items if item.status in OPEN]
    if claim:
        status = claim.get("status")
        blocked_on = claim.get("blocked_on")
    else:
        status = "working" if any(item.status == "doing" for item in items) else "idle"
        blocked_on = None
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}, got {status!r}")
    if status in ("waiting_on_zach", "blocked") and not blocked_on:
        raise ValueError(f"status {status!r} needs blocked_on (what it waits on)")
    top = sorted(open_items, key=lambda item: (item.status != "doing", item.proposed_t, item.id))[:3]
    days = config.window_days
    return {
        "schema": TEAM_SCHEMA,
        "member": config.name,
        "team": "Robot Team",
        "remit": config.remit,
        "kpi": {"name": f"working hours between {_levels_phrase(config.count_levels)} interventions ({days} d)",
                "value": kpi.mtbi_h, "unit": "h", "direction": "up", "target": kpi.next_gate_h,
                "level": kpi.level, "level_name": kpi.level_name, "next_gate": kpi.next_level,
                "on_track": growth is not None and growth.improving},
        "counter_kpi": {"name": f"working hours ({days} d)", "value": kpi.working_h, "unit": "h",
                        "owner": config.activity_agent},
        "status": status,
        "blocked_on": blocked_on,
        "todo": {"open": len(open_items),
                 "done_7d": sum(1 for item in items if item.done_t is not None and now - item.done_t <= WEEK_S),
                 "verified": sum(1 for item in items if item.status == "verified"),
                 "top": [item.title for item in top]},
        "links": dict(config.links),
        "updated_t": now,
    }
