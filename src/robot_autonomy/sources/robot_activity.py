"""The Robot Activity Agent's frozen files, read without importing robot_activity.

- `<root>/queue/events.jsonl` (`robot-activity-event/1`): its queue's `human` blockers are blocks, and asks.
- `<root>/samples/<robot>/<YYYY-MM-DD>.jsonl` (`robot-activity/1`): a fault streak a person had to clear is an
  intervention the robot itself observed; a short one that cleared by itself is a self-recovery (the good twin).
"""

from __future__ import annotations

from pathlib import Path

from .._files import read_jsonl
from ..hours import DEFAULT_SAMPLE_S, sample_files
from ..schema import Block, Intervention

DEFAULT_LABEL = ("remote", "answer_robot_question")


def _default_label(why: str) -> tuple[str, str]:
    return DEFAULT_LABEL


def read_blocks(root, *, label=None) -> list[Block]:
    """Replay the queue events: a human `block` opens a Block; an `unblock` that clears a human blocker, or the
    item's `finish` or `cancel`, closes it. Sorted by (since_t, id)."""
    label = label or _default_label
    open_by_item: dict[str, Block] = {}
    closed: list[Block] = []
    for event in read_jsonl(Path(root) / "queue" / "events.jsonl"):
        kind = event.get("kind")
        item_id = event.get("id")
        if item_id is None or event.get("t") is None:
            continue
        item_id = str(item_id)
        try:
            t = float(event["t"])
        except (TypeError, ValueError):
            continue
        if kind == "block":
            blocker = event.get("blocker") or {}
            if not isinstance(blocker, dict) or blocker.get("kind") != "human":
                continue
            if item_id in open_by_item:
                continue    # a second human block on an item that already has one open does not open another
            why = str(blocker.get("why") or "")
            level, cause = label(why)
            try:
                # The id uses the event's own t value as written, so it matches f"{item id}@{t}" for its writer.
                open_by_item[item_id] = Block(id=f"{item_id}@{event['t']}", robot=str(event.get("robot") or "any"),
                                              cause=cause, why=why, since_t=t, level=level, source="queue",
                                              item=item_id, by=event.get("by"))
            except (TypeError, ValueError):
                continue
        elif kind == "unblock":
            cleared = event.get("cleared") or []
            if any(isinstance(blocker, dict) and blocker.get("kind") == "human" for blocker in cleared):
                block = open_by_item.pop(item_id, None)
                if block is not None:
                    closed.append(_closed(block, t))
        elif kind in ("finish", "cancel"):
            block = open_by_item.pop(item_id, None)
            if block is not None:
                closed.append(_closed(block, t))
    blocks = closed + list(open_by_item.values())
    blocks.sort(key=lambda block: (block.since_t, block.id))
    return blocks


def _closed(block: Block, until_t: float) -> Block:
    return Block(id=block.id, robot=block.robot, cause=block.cause, why=block.why, since_t=block.since_t,
                 until_t=until_t, level=block.level, source=block.source, item=block.item, by=block.by)


def _health(row: dict) -> str:
    value = row.get("health")
    return "ok" if value is None else str(value)


def _sample_s(row: dict) -> float:
    value = row.get("sample_s")
    return DEFAULT_SAMPLE_S if value is None else float(value)


def read_faults(root, *, needs_human_after_s=600.0, tz=None) -> tuple[list[Intervention], list[dict]]:
    """Fault streaks per robot: maximal runs of consecutive rows (t gap <= 1.5 * sample_s) with health not "ok".
    Start = first row's t - sample_s, end = last row's t. Long enough -> a hands_on Intervention seen by the robot;
    shorter -> a self-recovery {"robot", "start_t", "end_t", "seconds", "health"}. A streak still open at the last
    row counts only once it is long enough."""
    interventions: list[Intervention] = []
    recoveries: list[dict] = []
    for robot, paths in sample_files(root).items():
        rows = []
        for path in paths:
            for row in read_jsonl(path):
                try:
                    rows.append((float(row["t"]), _sample_s(row), row))
                except (KeyError, TypeError, ValueError):
                    continue
        rows.sort(key=lambda entry: entry[0])
        streak: list[tuple[float, float, dict]] = []
        previous_t = None
        for t, sample_s, row in rows:
            consecutive = previous_t is not None and t - previous_t <= 1.5 * sample_s
            unhealthy = _health(row) != "ok"
            if streak and (not unhealthy or not consecutive):
                _emit_fault(robot, streak, closed=True, needs_human_after_s=needs_human_after_s,
                            interventions=interventions, recoveries=recoveries)
                streak = []
            if unhealthy:
                streak.append((t, sample_s, row))
            previous_t = t
        if streak:
            _emit_fault(robot, streak, closed=False, needs_human_after_s=needs_human_after_s,
                        interventions=interventions, recoveries=recoveries)
    interventions.sort(key=lambda intervention: (intervention.t, intervention.id))
    recoveries.sort(key=lambda recovery: (recovery["start_t"], recovery["robot"]))
    return interventions, recoveries


def _emit_fault(robot, streak, *, closed, needs_human_after_s, interventions, recoveries) -> None:
    first_t, first_sample_s, first_row = streak[0]
    start = first_t - first_sample_s
    end = streak[-1][0]
    seconds = end - start
    health = _health(first_row)
    if seconds >= needs_human_after_s:
        interventions.append(Intervention(
            id=f"fault:{robot}:{start:.0f}", t=start, robot=robot, level="hands_on", cause=health, source="robot",
            what=str(first_row.get("reason") or health), downtime_s=seconds))
    elif closed:
        recoveries.append({"robot": robot, "start_t": start, "end_t": end, "seconds": seconds, "health": health})
