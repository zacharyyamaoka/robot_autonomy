"""One plain-text screen: the level, the two readings, the causes, the todo list and the open blocks. No ANSI."""

from __future__ import annotations

import datetime as dt

from .. import _format
from ..backlog import Backlog


def window_days(kpi) -> int:
    return (dt.date.fromisoformat(kpi.end) - dt.date.fromisoformat(kpi.start)).days + 1


def level_line(kpi) -> str:
    """e.g. "R1 Assisted · 1.8 h between hands-on interventions (7 d) · next R2 at 8 h"."""
    days = window_days(kpi)
    if kpi.mtbi_h is None:
        middle = f"no working hours in the {days} d window"
    else:
        middle = f"{_format.hours(kpi.mtbi_h)} between hands-on interventions ({days} d)"
    upcoming = (f"next {kpi.next_level} at {_format.gate_hours(kpi.next_gate_h)}" if kpi.next_level
                else "top of the ladder")
    return f"{kpi.level} {kpi.level_name} · {middle} · {upcoming}"


def readings_line(kpi) -> str:
    parts = [f"{reading.name} {_format.hours(reading.mtbi_h)} ({reading.episodes} episodes)"
             for reading in kpi.readings]
    return "readings (the lower wins): " + " · ".join(parts)


def _effect_text(effect: dict | None) -> str:
    if not effect:
        return ""
    before, after = effect.get("before") or {}, effect.get("after") or {}
    drop = effect.get("drop")
    change = _format.percent(None if drop is None else -drop)
    return (f" — {_format.rate(before.get('per_100h'))} -> {_format.rate(after.get('per_100h'))} per 100 h "
            f"({change}, p {_format.p_value(effect.get('p_value'))})")


def render(kpi, cause_rows, backlog: Backlog, blocks, *, now) -> str:
    lines = [level_line(kpi), readings_line(kpi)]
    lines.append(f"window {kpi.start} .. {kpi.end} · {_format.hours(kpi.working_h)} worked · {kpi.counted} counted"
                 f" · hands-on {kpi.hands_on} · remote {kpi.remote} · commissioning {kpi.commissioning}"
                 f" · avoidable {kpi.avoidable} · unclocked {kpi.unclocked}")
    person = "—" if kpi.human_s_per_h is None else f"{kpi.human_s_per_h:.0f} person-s per working hour"
    lines.append(f"{person} · blocked {_format.hours(kpi.blocked_h)}")

    lines += ["", "Why they asked (episodes, avoidable, live improvement)"]
    if not cause_rows:
        lines.append("  (no interventions)")
    width = max([len(row.cause) for row in cause_rows] + [5])
    for row in cause_rows:
        live = backlog.live(row.cause)
        lines.append(f"  {row.level:<8}  {row.cause:<{width}}  {row.episodes:>4}  {row.avoidable:>4}  "
                     f"{live[0].id if live else '-'}")

    items = backlog.items()
    groups = (("doing", ("doing",), "[ ]"), ("proposed/accepted", ("proposed", "accepted"), "[ ]"),
              ("done (verifying)", ("done",), "[~]"), ("verified", ("verified",), "[x]"),
              ("ineffective", ("ineffective",), "[!]"))
    lines += ["", "Todo"]
    if not items:
        lines.append("  (empty)")
    for heading, statuses, mark in groups:
        group = [item for item in items if item.status in statuses]
        if not group:
            continue
        lines.append(f"  {heading}")
        for item in group:
            tail = _effect_text(item.effect) if item.status in ("verified", "ineffective") else ""
            status = f" [{item.status}]" if len(statuses) > 1 else ""
            lines.append(f"    {mark} {item.id} {item.title} ({item.owner}){status}{tail}")

    open_blocks = [block for block in blocks if block.until_t is None]
    lines += ["", "Open blocks"]
    if not open_blocks:
        lines.append("  (none)")
    for block in open_blocks:
        lines.append(f"  {block.robot}  {block.item or '-'}  waiting {_format.duration(block.seconds(now))}  "
                     f"{block.cause}: {block.why}")
    return "\n".join(lines) + "\n"
