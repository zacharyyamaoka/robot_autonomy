"""The denominator: hours each robot worked, per day ("total miles").

WHY activity hours by default: activity is the counterweight. An idle robot never needs a hand, so the KPI divides
only by hours the robot actually worked (the Robot Activity Agent's activity split).
"""

from __future__ import annotations

from pathlib import Path

from ._files import read_jsonl
from .schema import day_of

HoursTable = dict[tuple[str, str], float]    # {(robot, day): working hours}

DEFAULT_SAMPLE_S = 10.0
BASES = ("activity", "tracked")


def _sample_s(row: dict) -> float:
    value = row.get("sample_s")
    return DEFAULT_SAMPLE_S if value is None else float(value)


def _health(row: dict) -> str:
    value = row.get("health")
    return "ok" if value is None else str(value)


def is_activity(row: dict) -> bool:
    """robot_activity's activity split: claimed, an item dispatched, and health "ok" (a missing key reads as null)."""
    return row.get("claimed_by") is not None and row.get("item") is not None and _health(row) == "ok"


def sample_files(root) -> dict[str, list[Path]]:
    """{robot: [<root>/samples/<robot>/<day>.jsonl, ...]} in name order; {} when the folder is missing."""
    samples = Path(root) / "samples"
    if not samples.is_dir():
        return {}
    return {robot_dir.name: sorted(robot_dir.glob("*.jsonl"))
            for robot_dir in sorted(samples.iterdir()) if robot_dir.is_dir()}


def from_robot_activity(root, *, basis: str = "activity", tz: str | None = None) -> HoursTable:
    """Hours per (robot, day) from robot_activity's frozen `robot-activity/1` sample files.

    Each row weighs sample_s seconds (default 10). basis "activity": rows claimed, with an item and health "ok";
    "tracked": every row. The day is day_of(row["t"], tz), not the file name.
    """
    if basis not in BASES:
        raise ValueError(f"basis must be one of {BASES}, got {basis!r}")
    table: HoursTable = {}
    for robot, paths in sample_files(root).items():
        # SPEC: the robot is the <robot> folder the file sits in (the path names it; only the day is re-derived).
        for path in paths:
            for row in read_jsonl(path):
                try:
                    t = float(row["t"])
                    weight_s = _sample_s(row)
                except (KeyError, TypeError, ValueError):
                    continue
                if basis == "activity" and not is_activity(row):
                    continue
                key = (robot, day_of(t, tz))
                table[key] = table.get(key, 0.0) + weight_s / 3600.0
    return table


def from_jsonl(path) -> HoursTable:
    """Rows {"robot", "day", "hours"}; rows for the same key add up. A missing file gives {}."""
    table: HoursTable = {}
    for row in read_jsonl(path):
        try:
            key = (str(row["robot"]), str(row["day"]))
            hours = float(row["hours"])
        except (KeyError, TypeError, ValueError):
            continue
        table[key] = table.get(key, 0.0) + hours
    return table


def _in(value, allowed) -> bool:
    return allowed is None or value in allowed


def _as_set(values):
    if values is None:
        return None
    if isinstance(values, str):
        return {values}
    return set(values)


def total(table, *, robots=None, days=None) -> float:
    robots, days = _as_set(robots), _as_set(days)
    return float(sum(hours for (robot, day), hours in table.items() if _in(robot, robots) and _in(day, days)))


def per_day(table, *, robots=None) -> dict[str, float]:
    """{day: hours summed over robots in scope}."""
    robots = _as_set(robots)
    out: dict[str, float] = {}
    for (robot, day), hours in table.items():
        if _in(robot, robots):
            out[day] = out.get(day, 0.0) + hours
    return out


def days_with_hours(table, *, robots=None) -> set[str]:
    """Days whose hours (summed over robots) > 0."""
    return {day for day, hours in per_day(table, robots=robots).items() if hours > 0}
