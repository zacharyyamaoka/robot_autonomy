"""sources/robot_activity.py: the Robot Activity Agent's frozen files (SPEC "sources/").

Inputs follow robot_activity's frozen shapes: samples are `robot-activity/1` rows, queue events are
`robot-activity-event/1` lines (/home/bam/robot_activity/docs/SPEC.md, "The events ledger — a frozen, cross-repo seam").
"""

from __future__ import annotations

import pytest

from helpers import t_of, write_jsonl
from robot_autonomy.schema import Block, Intervention
from robot_autonomy.sources.robot_activity import read_blocks, read_faults

EVENT_SCHEMA = "robot-activity-event/1"


def event(kind, item_id, t, *, robot="any", by=None, **extra) -> dict:
    return {"schema": EVENT_SCHEMA, "t": t, "kind": kind, "id": item_id, "robot": robot, "by": by, **extra}


def blocker(kind, why, eta_t=None) -> dict:
    return {"kind": kind, "why": why, "eta_t": eta_t}


@pytest.fixture
def queue_root(tmp_path):
    root = tmp_path / "activity"
    # Fractional times so f"{item}@{t}" has one spelling.
    write_jsonl(
        root / "queue" / "events.jsonl",
        [
            event("submit", "q1", 1000.5, by="pick agent", item={"id": "q1", "title": "pick vials"}),
            event("block", "q1", 1100.5, by="pick agent", blocker=blocker("human", "replug the camera")),
            # a second human block on an item that already has one open: no new block
            event("block", "q1", 1200.5, by="pick agent", blocker=blocker("human", "still: replug the camera")),
            event("block", "q1", 1250.5, by="pick agent", blocker=blocker("code", "fix the planner")),
            # clearing only the code blocker leaves the human block open
            event("unblock", "q1", 1300.5, by="dev agent", blocker_kind="code",
                  cleared=[blocker("code", "fix the planner")]),
            # clearing all, a human one among them: closes
            event("unblock", "q1", 1400.5, by="Zach", blocker_kind=None,
                  cleared=[blocker("human", "replug the camera")]),
            event("block", "q1", 1500.5, by="pick agent", blocker=blocker("human", "approve the run")),
            event("start", "q1", 1600.5, robot="arm-1", by="pick agent"),
            # finish closes the open human block
            event("finish", "q1", 1700.5, robot="arm-1", by="pick agent", result="ok", note=None),
            # q2: still open at the end
            event("block", "q2", 2000.5, robot="arm-2", by="sort agent", blocker=blocker("human", "clear the tray")),
            # q3: cancel closes
            event("block", "q3", 2100.5, by="sort agent", blocker=blocker("human", "which bin?")),
            event("cancel", "q3", 2200.5, by="sort agent", note="not needed"),
            # q4: a non-human block never opens anything
            event("block", "q4", 2300.5, by="sort agent", blocker=blocker("hardware", "gripper pad worn")),
        ],
    )
    return root


def test_read_blocks_opens_and_closes_human_blocks(queue_root):
    blocks = {block.id: block for block in read_blocks(queue_root)}
    assert set(blocks) == {"q1@1100.5", "q1@1500.5", "q2@2000.5", "q3@2100.5"}
    assert all(isinstance(block, Block) for block in blocks.values())
    first = blocks["q1@1100.5"]
    assert first.robot == "any"  # the event's robot
    assert first.item == "q1"
    assert first.why == "replug the camera"
    assert first.since_t == 1100.5
    assert first.until_t == 1400.5
    assert first.by == "pick agent"
    assert first.source == "queue"
    assert (first.level, first.cause) == ("remote", "answer_robot_question")  # the default label
    assert blocks["q1@1500.5"].until_t == 1700.5  # closed by finish
    assert blocks["q1@1500.5"].why == "approve the run"
    assert blocks["q2@2000.5"].until_t is None
    assert blocks["q2@2000.5"].robot == "arm-2"
    assert blocks["q2@2000.5"].by == "sort agent"
    assert blocks["q3@2100.5"].until_t == 2200.5  # closed by cancel


def test_read_blocks_label(queue_root):
    def label(why):
        return ("hands_on", "power_or_replug") if "replug" in why else ("remote", "approve_robot_run")

    blocks = {block.id: block for block in read_blocks(queue_root, label=label)}
    assert (blocks["q1@1100.5"].level, blocks["q1@1100.5"].cause) == ("hands_on", "power_or_replug")
    assert (blocks["q1@1500.5"].level, blocks["q1@1500.5"].cause) == ("remote", "approve_robot_run")


def test_read_blocks_with_an_empty_events_file(tmp_path):
    root = tmp_path / "activity"
    write_jsonl(root / "queue" / "events.jsonl", [])
    assert read_blocks(root) == []


# --------------------------------------------------------------------------------------------- faults

BASE = t_of("2026-11-01", 23.5)  # 23:30 UTC; streak B crosses midnight into 2026-11-02


def _sample(t, robot="arm-1", **fields) -> dict:
    row = {"schema": "robot-activity/1", "t": t, "robot": robot, "sample_s": 10.0, "claimed_by": "pick agent",
           "item": "q1"}
    row.update(fields)
    return row


def _write_samples(root, rows):
    """Each row goes in samples/<robot>/<UTC day of t>.jsonl, the way robot_activity's Store files them."""
    from datetime import datetime, timezone

    for row in rows:
        day = datetime.fromtimestamp(row["t"], tz=timezone.utc).date().isoformat()
        write_jsonl(root / "samples" / row["robot"] / f"{day}.jsonl", [row])


@pytest.fixture
def fault_root(tmp_path):
    root = tmp_path / "activity"
    rows = [_sample(BASE + offset, health="ok") for offset in (0, 10, 20)]
    # streak A: estop at +30, +40, +50 -> start +20 (first t - sample_s), end +50: 30 s, a self-recovery
    rows += [_sample(BASE + offset, health="estop") for offset in (30, 40, 50)]
    rows += [_sample(BASE + 60)]  # health key missing: reads as "ok"
    # streak B: 61 rows +1500 .. +2100 (crosses midnight at +1800); the first row's health and reason name it,
    # a later row's different health keeps the streak going -> start +1490, end +2100: 610 s >= 600
    for offset in range(1500, 2101, 10):
        if offset == 1500:
            rows.append(_sample(BASE + offset, health="fault", reason="BUS DOWN (rig fault)"))
        elif offset == 1800:
            rows.append(_sample(BASE + offset, health="disconnected"))
        else:
            rows.append(_sample(BASE + offset, health="fault"))
    rows.append(_sample(BASE + 2110, health="ok"))
    # streak C: +3000, +3010, +3025 (gaps 10 and 15 <= 1.5 x 10) -> one streak, start +2990, end +3025: 35 s;
    # then +3041 (gap 16 > 15) -> a new streak, start +3031, end +3041: 10 s
    rows += [_sample(BASE + offset, health="fault") for offset in (3000, 3010, 3025, 3041)]
    rows.append(_sample(BASE + 3050, health="ok"))
    _write_samples(root, rows)
    return root


def _recovery_key(recovery):
    return (recovery["robot"], recovery["start_t"])


def test_read_faults_long_streak_is_an_intervention(fault_root):
    interventions, _recoveries = read_faults(fault_root, needs_human_after_s=600.0, tz="UTC")
    assert len(interventions) == 1
    [fault] = interventions
    assert isinstance(fault, Intervention)
    start = BASE + 1490
    assert fault.id == f"fault:arm-1:{start:.0f}"
    assert fault.t == pytest.approx(start)
    assert fault.robot == "arm-1"
    assert fault.level == "hands_on"
    assert fault.cause == "fault"  # the first row's health
    assert fault.source == "robot"
    assert fault.reading == "observed"
    assert fault.what == "BUS DOWN (rig fault)"  # the first row's reason
    assert fault.downtime_s == pytest.approx(610.0)


def test_read_faults_short_streaks_are_self_recoveries(fault_root):
    _interventions, recoveries = read_faults(fault_root, needs_human_after_s=600.0, tz="UTC")
    assert sorted(recoveries, key=_recovery_key) == [
        {"robot": "arm-1", "start_t": BASE + 20, "end_t": BASE + 50, "seconds": 30.0, "health": "estop"},
        {"robot": "arm-1", "start_t": BASE + 2990, "end_t": BASE + 3025, "seconds": 35.0, "health": "fault"},
        {"robot": "arm-1", "start_t": BASE + 3031, "end_t": BASE + 3041, "seconds": 10.0, "health": "fault"},
    ]


def test_read_faults_threshold(fault_root):
    interventions, recoveries = read_faults(fault_root, needs_human_after_s=30.0, tz="UTC")
    # streak A (30 s), B (610 s) and the first half of C (35 s) are long enough; C's tail (10 s) is not
    assert sorted(iv.id for iv in interventions) == sorted(
        f"fault:arm-1:{start:.0f}" for start in (BASE + 20, BASE + 1490, BASE + 2990)
    )
    by_id = {iv.id: iv for iv in interventions}
    assert by_id[f"fault:arm-1:{BASE + 20:.0f}"].what == "estop"  # no reason: the health
    assert by_id[f"fault:arm-1:{BASE + 20:.0f}"].cause == "estop"
    assert [recovery["seconds"] for recovery in recoveries] == [10.0]


def test_read_faults_open_streaks_and_the_gap_rule(tmp_path):
    root = tmp_path / "activity"
    start = t_of("2026-11-05", 8.0)
    rows = []
    # arm-2: a short streak still open at the last row -> not an intervention (yet)
    rows += [_sample(start, robot="arm-2", health="ok")]
    rows += [_sample(start + offset, robot="arm-2", health="fault") for offset in range(10, 301, 10)]
    # arm-3: a long streak still open at the last row (start, end start+700: 700 s) -> an intervention
    rows += [_sample(start, robot="arm-3", health="ok")]
    rows += [_sample(start + offset, robot="arm-3", health="fault") for offset in range(10, 701, 10)]
    # arm-4: 810 s of faults with one 20 s gap: two streaks (410 s and 390 s), neither long enough
    rows += [_sample(start + offset, robot="arm-4", health="fault") for offset in range(0, 401, 10)]
    rows += [_sample(start + offset, robot="arm-4", health="fault") for offset in range(420, 801, 10)]
    rows += [_sample(start + 810, robot="arm-4", health="ok")]
    _write_samples(root, rows)
    interventions, recoveries = read_faults(root, needs_human_after_s=600.0, tz="UTC")
    assert [iv.id for iv in interventions] == [f"fault:arm-3:{start:.0f}"]
    assert interventions[0].downtime_s == pytest.approx(700.0)
    assert interventions[0].robot == "arm-3"
    arm_4 = sorted((recovery for recovery in recoveries if recovery["robot"] == "arm-4"), key=_recovery_key)
    assert arm_4 == [
        {"robot": "arm-4", "start_t": start - 10, "end_t": start + 400, "seconds": 410.0, "health": "fault"},
        {"robot": "arm-4", "start_t": start + 410, "end_t": start + 800, "seconds": 390.0, "health": "fault"},
    ]
    assert not any(recovery["robot"] == "arm-3" for recovery in recoveries)


def test_read_faults_sample_s_defaults_to_ten(tmp_path):
    root = tmp_path / "activity"
    start = t_of("2026-11-05", 8.0)
    rows = [{"schema": "robot-activity/1", "t": start + offset, "robot": "arm-5", "health": "fault"}
            for offset in (0, 10, 20)]
    rows.append({"schema": "robot-activity/1", "t": start + 30, "robot": "arm-5"})
    _write_samples(root, rows)
    interventions, recoveries = read_faults(root, needs_human_after_s=600.0, tz="UTC")
    assert interventions == []
    # start = first t - 10 (default sample_s), end = last fault t
    assert recoveries == [
        {"robot": "arm-5", "start_t": start - 10, "end_t": start + 20, "seconds": 30.0, "health": "fault"}
    ]
