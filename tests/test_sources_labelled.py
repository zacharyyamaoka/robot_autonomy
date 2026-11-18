"""sources/labelled.py: any labelled log of asks (SPEC "sources/")."""

from __future__ import annotations

import pytest

from helpers import t_of, write_jsonl
from robot_autonomy.schema import Intervention
from robot_autonomy.sources.labelled import read_labelled


def _rows():
    return [
        {
            "id": "ask-1",
            "t": t_of("2026-11-02", 10.0),
            "level": "hands_on",
            "cause": "power_or_replug",
            "robot": "arm-1",
            "what": "replug the overhead camera",
            "avoidable": "yes",
            "fix": "powered hub",
            "thread": "chat-1",
            "by": "pick agent",
            "human_s": 120.0,
            "downtime_s": 900.0,
            "evidence": "transcript.jsonl#L40",
        },
        # `ts` with Z, `category` instead of cause, `session` instead of thread, robot from the default
        {"id": "ask-2", "ts": "2026-11-02T11:00:00Z", "level": "remote", "category": "approve_robot_run",
         "session": "sess-9"},
        # level "none": not an intervention, skipped
        {"id": "ask-3", "t": t_of("2026-11-02", 12.0), "level": "none", "cause": "code_review"},
        # an avoidable outside AVOIDABLE reads as "unclear"
        {"id": "ask-4", "t": t_of("2026-11-02", 13.0), "level": "hands_on", "cause": "inspect", "avoidable": "maybe"},
    ]


def test_rows_become_interventions():
    interventions = read_labelled(_rows(), robot="arm-9", tz="UTC")
    assert all(isinstance(iv, Intervention) for iv in interventions)
    by_id = {iv.id: iv for iv in interventions}
    assert set(by_id) == {"ask-1", "ask-2", "ask-4"}
    first = by_id["ask-1"]
    assert first.t == t_of("2026-11-02", 10.0)
    assert first.level == "hands_on"
    assert first.cause == "power_or_replug"
    assert first.robot == "arm-1"  # the row's robot wins over the default
    assert first.source == "chat"
    assert first.reading == "asked"
    assert first.what == "replug the overhead camera"
    assert first.avoidable == "yes"
    assert first.fix == "powered hub"
    assert first.thread == "chat-1"
    assert first.by == "pick agent"
    assert first.human_s == 120.0
    assert first.downtime_s == 900.0
    assert first.evidence == "transcript.jsonl#L40"
    second = by_id["ask-2"]
    assert second.t == t_of("2026-11-02", 11.0)
    assert second.cause == "approve_robot_run"
    assert second.thread == "sess-9"
    assert second.robot == "arm-9"
    assert second.level == "remote"
    assert by_id["ask-4"].avoidable == "unclear"


def test_reads_a_jsonl_path(tmp_path):
    path = write_jsonl(tmp_path / "labels.jsonl", _rows())
    from_path = read_labelled(path, robot="arm-9", tz="UTC")
    from_rows = read_labelled(_rows(), robot="arm-9", tz="UTC")
    assert sorted(from_path, key=lambda iv: iv.id) == sorted(from_rows, key=lambda iv: iv.id)
    assert sorted(iv.id for iv in read_labelled(str(path), robot="arm-9", tz="UTC")) == ["ask-1", "ask-2", "ask-4"]


def test_source_and_reading_arguments():
    operator = read_labelled(_rows(), source="operator", robot="arm-9", tz="UTC")
    assert {iv.source for iv in operator} == {"operator"}
    assert {iv.reading for iv in operator} == {"observed"}
    forced = read_labelled(_rows(), source="chat", reading="observed", robot="arm-9", tz="UTC")
    assert {iv.reading for iv in forced} == {"observed"}


@pytest.mark.parametrize(
    ("timestamp", "tz", "expected_t"),
    [
        ("2026-11-02T10:00:00Z", "UTC", t_of("2026-11-02", 10.0)),
        ("2026-11-02T10:00:00Z", "Asia/Tokyo", t_of("2026-11-02", 10.0)),  # Z wins over tz
        ("2026-11-02T19:00:00+09:00", "UTC", t_of("2026-11-02", 10.0)),  # an explicit offset
        ("2026-11-02T10:00:00", "UTC", t_of("2026-11-02", 10.0)),  # naive, read in tz
        ("2026-11-02T10:00:00", "Asia/Tokyo", t_of("2026-11-02", 1.0)),  # naive 10:00 JST = 01:00 UTC
    ],
)
def test_iso_timestamps(timestamp, tz, expected_t):
    [intervention] = read_labelled(
        [{"id": "x", "ts": timestamp, "level": "hands_on", "cause": "inspect", "robot": "arm-1"}], tz=tz
    )
    assert intervention.t == pytest.approx(expected_t)
