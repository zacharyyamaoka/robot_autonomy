"""ledger.py: the append-only interventions and blocks files (SPEC "ledger.py — files a human can read")."""

from __future__ import annotations

import json

import pytest

from helpers import file_lines, make_intervention, read_jsonl, t_of
from robot_autonomy.ledger import Ledger
from robot_autonomy.schema import Block, Intervention


def test_record_writes_one_line_per_intervention_with_the_schema(autonomy_root):
    ledger = Ledger(autonomy_root)
    intervention = make_intervention("iv-1", t_of("2026-11-02", 10.0), what="replug the camera", by="pick agent")
    assert ledger.record(intervention) is True
    lines = read_jsonl(autonomy_root / "interventions.jsonl")
    assert len(lines) == 1
    assert lines[0]["schema"] == "robot-autonomy/1"
    assert lines[0] == intervention.to_dict()
    assert (autonomy_root / ".lock").exists()


def test_record_is_idempotent_on_the_id(autonomy_root):
    ledger = Ledger(autonomy_root)
    intervention = make_intervention("iv-1", t_of("2026-11-02", 10.0))
    assert ledger.record(intervention) is True
    before = (autonomy_root / "interventions.jsonl").read_bytes()
    # Same id, even with different content: no write.
    assert ledger.record(intervention) is False
    assert ledger.record(make_intervention("iv-1", t_of("2026-11-03", 10.0), cause="inspect")) is False
    assert (autonomy_root / "interventions.jsonl").read_bytes() == before
    assert len(file_lines(autonomy_root / "interventions.jsonl")) == 1
    assert [iv.id for iv in ledger.interventions()] == ["iv-1"]


def test_record_many_returns_the_number_written(autonomy_root):
    ledger = Ledger(autonomy_root)
    ledger.record(make_intervention("iv-1", t_of("2026-11-02", 1.0)))
    written = ledger.record_many(
        [
            make_intervention("iv-1", t_of("2026-11-02", 1.0)),  # already there
            make_intervention("iv-2", t_of("2026-11-02", 2.0)),
            make_intervention("iv-3", t_of("2026-11-02", 3.0)),
        ]
    )
    assert written == 2
    assert len(file_lines(autonomy_root / "interventions.jsonl")) == 3


def test_interventions_are_sorted_by_t_then_id_and_filtered(autonomy_root):
    ledger = Ledger(autonomy_root)
    noon = t_of("2026-11-02", 12.0)
    ledger.record(make_intervention("iv-c", noon + 60.0))
    ledger.record(make_intervention("iv-b", noon, robot="arm-2"))
    ledger.record(make_intervention("iv-a", noon))
    ledger.record(make_intervention("iv-d", noon - 60.0))
    assert [iv.id for iv in ledger.interventions()] == ["iv-d", "iv-a", "iv-b", "iv-c"]
    # since <= t < until
    assert [iv.id for iv in ledger.interventions(since=noon, until=noon + 60.0)] == ["iv-a", "iv-b"]
    assert [iv.id for iv in ledger.interventions(since=noon + 60.0)] == ["iv-c"]
    assert [iv.id for iv in ledger.interventions(until=noon)] == ["iv-d"]
    assert [iv.id for iv in ledger.interventions(robot="arm-2")] == ["iv-b"]
    assert all(isinstance(iv, Intervention) for iv in ledger.interventions())


def test_round_trip_through_the_file_keeps_every_field(autonomy_root):
    intervention = make_intervention(
        "iv-1",
        t_of("2026-11-02", 10.0),
        level="remote",
        cause="approve_robot_run",
        what="give the go",
        avoidable="yes",
        fix="auto-approve",
        thread="chat-1",
        by="pick agent",
        human_s=30.0,
        downtime_s=120.0,
        evidence="transcript#L3",
    )
    Ledger(autonomy_root).record(intervention)
    assert Ledger(autonomy_root).interventions() == [intervention]


def test_retract_writes_a_retraction_line_and_hides_the_intervention(autonomy_root):
    ledger = Ledger(autonomy_root)
    ledger.record(make_intervention("iv-1", t_of("2026-11-02", 1.0)))
    ledger.record(make_intervention("iv-2", t_of("2026-11-02", 2.0)))
    retract_t = t_of("2026-11-03", 9.0)
    ledger.retract("iv-1", why="a code review, not an intervention", by="Zach", now=retract_t)
    lines = read_jsonl(autonomy_root / "interventions.jsonl")
    assert len(lines) == 3  # append-only: nothing rewritten
    retraction = lines[-1]
    assert retraction["schema"] == "robot-autonomy/1"
    assert retraction["retract"] == "iv-1"
    assert retraction["t"] == retract_t
    assert retraction["by"] == "Zach"
    assert retraction["why"] == "a code review, not an intervention"
    assert [iv.id for iv in ledger.interventions()] == ["iv-2"]
    # A retracted id is still "already recorded": no write.
    assert ledger.record(make_intervention("iv-1", t_of("2026-11-02", 1.0))) is False
    assert len(file_lines(autonomy_root / "interventions.jsonl")) == 3


def test_retract_of_an_unknown_id_raises_key_error(autonomy_root):
    ledger = Ledger(autonomy_root)
    with pytest.raises(KeyError):
        ledger.retract("never-recorded", why="typo")


def test_unparsable_lines_are_skipped(autonomy_root):
    ledger = Ledger(autonomy_root)
    ledger.record(make_intervention("iv-1", t_of("2026-11-02", 1.0)))
    with (autonomy_root / "interventions.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("{this is not json\n")
        handle.write("\n")
    ledger.record(make_intervention("iv-2", t_of("2026-11-02", 2.0)))
    assert [iv.id for iv in Ledger(autonomy_root).interventions()] == ["iv-1", "iv-2"]


def test_a_second_ledger_on_the_same_root_sees_every_change(autonomy_root):
    writer = Ledger(autonomy_root)
    reader = Ledger(autonomy_root)
    assert reader.interventions() == []
    writer.record(make_intervention("iv-1", t_of("2026-11-02", 1.0)))
    assert [iv.id for iv in reader.interventions()] == ["iv-1"]
    # The reader also knows the id is taken.
    assert reader.record(make_intervention("iv-1", t_of("2026-11-02", 1.0))) is False
    writer.retract("iv-1", why="duplicate")
    assert reader.interventions() == []
    writer.open_block(Block(id="b1", robot="arm-1", cause="approve_robot_run", why="go?", since_t=1.0), count=False)
    assert [block.id for block in reader.blocks()] == ["b1"]
    writer.close_block("b1", until_t=5.0)
    assert reader.blocks()[0].until_t == 5.0


def _block(block_id="b1", since_t=None, **fields) -> Block:
    defaults = {
        "robot": "arm-1",
        "cause": "approve_robot_run",
        "why": "approve the pick run",
        "since_t": t_of("2026-11-02", 9.0) if since_t is None else since_t,
        "item": "q7",
        "by": "pick agent",
    }
    defaults.update(fields)
    return Block(id=block_id, **defaults)


def test_open_block_writes_an_open_event_and_counts_the_ask(autonomy_root):
    ledger = Ledger(autonomy_root)
    block = _block(level="remote", source="queue")
    assert ledger.open_block(block) is True
    events = read_jsonl(autonomy_root / "blocks.jsonl")
    assert events == [{"schema": "robot-autonomy/1", "event": "open", "block": block.to_dict()}]
    # count=True records the ask as an Intervention.
    [ask] = ledger.interventions()
    assert ask.id == "block:b1"
    assert ask.t == block.since_t
    assert ask.robot == "arm-1"
    assert ask.level == "remote"
    assert ask.cause == "approve_robot_run"
    assert ask.source == "queue"
    assert ask.reading == "asked"  # SOURCE_READING["queue"]
    assert ask.what == "approve the pick run"
    assert ask.thread == "b1"
    assert ask.by == "pick agent"
    assert ledger.blocks() == [block]


def test_open_block_without_counting_writes_no_intervention(autonomy_root):
    ledger = Ledger(autonomy_root)
    assert ledger.open_block(_block(), count=False) is True
    assert ledger.interventions() == []
    assert not (autonomy_root / "interventions.jsonl").exists() or file_lines(autonomy_root / "interventions.jsonl") == []


def test_open_block_twice_is_a_no_op(autonomy_root):
    ledger = Ledger(autonomy_root)
    assert ledger.open_block(_block()) is True
    blocks_before = (autonomy_root / "blocks.jsonl").read_bytes()
    interventions_before = (autonomy_root / "interventions.jsonl").read_bytes()
    assert ledger.open_block(_block()) is False
    assert (autonomy_root / "blocks.jsonl").read_bytes() == blocks_before
    assert (autonomy_root / "interventions.jsonl").read_bytes() == interventions_before
    # Closed ids cannot reopen either.
    ledger.close_block("b1", until_t=t_of("2026-11-02", 10.0))
    blocks_after_close = (autonomy_root / "blocks.jsonl").read_bytes()
    assert ledger.open_block(_block()) is False
    assert (autonomy_root / "blocks.jsonl").read_bytes() == blocks_after_close


def test_close_block_writes_a_close_event_and_returns_the_closed_block(autonomy_root):
    ledger = Ledger(autonomy_root)
    block = _block()
    ledger.open_block(block, count=False)
    until_t = t_of("2026-11-02", 10.5)
    closed = ledger.close_block("b1", until_t=until_t, by="Zach")
    assert isinstance(closed, Block)
    assert closed.id == "b1"
    assert closed.until_t == until_t
    assert closed.since_t == block.since_t
    assert closed.why == block.why
    events = read_jsonl(autonomy_root / "blocks.jsonl")
    assert len(events) == 2
    assert events[1] == {"schema": "robot-autonomy/1", "event": "close", "id": "b1", "until_t": until_t, "by": "Zach"}
    # Closing again: returned unchanged, no write.
    again = ledger.close_block("b1", until_t=until_t + 999.0, by="someone else")
    assert again.until_t == until_t
    assert len(read_jsonl(autonomy_root / "blocks.jsonl")) == 2


def test_close_unknown_block_raises_key_error(autonomy_root):
    with pytest.raises(KeyError):
        Ledger(autonomy_root).close_block("nope", until_t=1.0)


def test_blocks_replay_filter_and_sort(autonomy_root):
    ledger = Ledger(autonomy_root)
    morning = t_of("2026-11-02", 9.0)
    ledger.open_block(_block("b-late", since_t=morning + 600.0), count=False)
    ledger.open_block(_block("b-early", since_t=morning, robot="arm-2"), count=False)
    ledger.open_block(_block("b-same-a", since_t=morning + 300.0), count=False)
    ledger.open_block(_block("b-same-0", since_t=morning + 300.0), count=False)
    ledger.close_block("b-late", until_t=morning + 900.0)
    assert [block.id for block in ledger.blocks()] == ["b-early", "b-same-0", "b-same-a", "b-late"]
    assert [block.id for block in ledger.blocks(open_only=True)] == ["b-early", "b-same-0", "b-same-a"]
    assert [block.id for block in ledger.blocks(robot="arm-2")] == ["b-early"]
    assert [block.id for block in ledger.blocks(open_only=True, robot="arm-1")] == ["b-same-0", "b-same-a"]
    closed = [block for block in ledger.blocks() if block.id == "b-late"][0]
    assert closed.until_t == morning + 900.0


def test_blocks_file_skips_unparsable_lines(autonomy_root):
    ledger = Ledger(autonomy_root)
    ledger.open_block(_block("b1"), count=False)
    with (autonomy_root / "blocks.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("garbage\n")
    ledger.open_block(_block("b2"), count=False)
    assert sorted(block.id for block in Ledger(autonomy_root).blocks()) == ["b1", "b2"]


def test_files_are_plain_json_lines(autonomy_root):
    ledger = Ledger(autonomy_root)
    ledger.record(make_intervention("iv-1", t_of("2026-11-02", 1.0)))
    ledger.open_block(_block(), count=True)
    for name in ("interventions.jsonl", "blocks.jsonl"):
        for line in file_lines(autonomy_root / name):
            row = json.loads(line)
            assert row["schema"] == "robot-autonomy/1"
