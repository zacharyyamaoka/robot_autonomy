"""demo.py: a deterministic simulated program that exercises every source (SPEC "demo.py")."""

from __future__ import annotations

import shutil
from collections import Counter

import pytest

from helpers import read_jsonl, snapshot_tree
from robot_autonomy import demo
from robot_autonomy.agent import load_config
from robot_autonomy.backlog import Backlog
from robot_autonomy.ledger import Ledger
from robot_autonomy.metrics import ROBOT_LADDER

LEVEL_ORDER = [gate.level for gate in ROBOT_LADDER]


@pytest.fixture(scope="module")
def demo_run(tmp_path_factory):
    """simulate() twice into the SAME path (so absolute paths written into files match), default arguments.

    Returns (root, first result, first snapshot, second result, second snapshot); the root holds the second run.
    """
    root = tmp_path_factory.mktemp("demo") / "program"
    first_result = demo.simulate(root, seed=0)
    first_files = snapshot_tree(root)
    shutil.rmtree(root)
    second_result = demo.simulate(root, seed=0)
    second_files = snapshot_tree(root)
    return root, first_result, first_files, second_result, second_files


def test_same_seed_gives_byte_identical_files(demo_run):
    _root, first_result, first_files, second_result, second_files = demo_run
    assert first_result == second_result
    assert sorted(first_files) == sorted(second_files)
    different = [path for path in first_files if first_files[path] != second_files[path]]
    assert different == []


def test_the_result_summary(demo_run):
    root, result, _first_files, _second_result, _second_files = demo_run
    assert set(result) >= {"days", "interventions", "episodes", "improvements", "level_start", "level_end"}
    assert result["days"] == 56
    assert result["interventions"] > 0
    assert 0 < result["episodes"] <= result["interventions"]
    statuses = Counter(item.status for item in Backlog(root).items())
    assert {status: count for status, count in result["improvements"].items() if count} == dict(statuses)


def test_the_kpi_climbs_at_least_one_rung(demo_run):
    _root, result, *_ = demo_run
    assert LEVEL_ORDER.index(result["level_end"]) > LEVEL_ORDER.index(result["level_start"])


def test_fixes_end_verified_and_one_ends_ineffective(demo_run):
    root, result, *_ = demo_run
    assert result["improvements"].get("verified", 0) >= 1
    assert result["improvements"].get("ineffective", 0) >= 1
    board = Backlog(root)
    for item in board.items(status=("verified", "ineffective")):
        assert item.evidence, "a finished fix carries evidence"
        assert isinstance(item.effect, dict)
        assert item.done_t is not None and item.verified_t is not None
    # only verify.assess (through the agent's record_effect) settles a fix: every settled item has an "effect" event
    effect_ids = {event["id"] for event in board.events() if event["kind"] == "effect"}
    assert {item.id for item in board.items(status=("verified", "ineffective"))} <= effect_ids


def test_every_source_is_exercised(demo_run):
    root, *_ = demo_run
    for relative in ("hours.jsonl", "sources/chat.jsonl", "sources/tickets.csv", "sources/diag.jsonl",
                     "activity/queue/events.jsonl", "autonomy.toml"):
        assert (root / relative).is_file(), relative
    interventions = Ledger(root).interventions()
    sources = {iv.source for iv in interventions}
    assert {"chat", "operator", "field_service", "diagnostics", "queue"} <= sources
    assert {iv.reading for iv in interventions} == {"asked", "observed"}
    # chat asks repeat inside threads
    chat_threads = Counter(iv.thread for iv in interventions if iv.source == "chat" and iv.thread)
    assert chat_threads and max(chat_threads.values()) >= 2
    # human blockers in the robot_activity-shaped folder
    events = read_jsonl(root / "activity" / "queue" / "events.jsonl")
    assert all(event["schema"] == "robot-activity-event/1" for event in events)
    assert any(event["kind"] == "block" and event["blocker"]["kind"] == "human" for event in events)
    assert Ledger(root).blocks()


def test_diagnostics_are_valid_records_with_long_errors_and_self_recoveries(demo_run):
    root, *_ = demo_run
    from robot_msgs.std import DiagnosticRecord

    from robot_autonomy.sources.diagnostics import read_diagnostics

    for line in read_jsonl(root / "sources" / "diag.jsonl"):
        DiagnosticRecord.from_dict(line)
    interventions, recoveries = read_diagnostics(root / "sources" / "diag.jsonl")
    assert interventions
    assert recoveries


def test_the_config_names_every_source(demo_run):
    root, *_ = demo_run
    config = load_config(root / "autonomy.toml")
    assert {"labelled", "robot_activity", "diagnostics", "field_service"} <= {source.kind for source in config.sources}
    assert config.hours is not None


def test_the_agent_ran_daily(demo_run):
    root, result, *_ = demo_run
    rows = read_jsonl(root / "agent" / "kpi.jsonl")
    assert len(rows) >= result["days"] - 1
    ends = [row["end"] for row in rows]
    assert ends == sorted(ends) and len(ends) == len(set(ends))
    assert (root / "team" / "status.json").is_file()
    assert read_jsonl(root / "agent" / "actions.jsonl")


def test_days_and_seed_arguments(tmp_path):
    result = demo.simulate(tmp_path / "short", days=14, seed=1, start="2026-10-01", robots=("cell-a",), tz="UTC")
    assert result["days"] == 14
    rows = read_jsonl(tmp_path / "short" / "agent" / "kpi.jsonl")
    assert 0 < len(rows) <= 14
    assert all("2026-09-30" <= row["end"] <= "2026-10-14" for row in rows)
