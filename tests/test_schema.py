"""schema.py: constants, day_of, Intervention and Block (SPEC "schema.py")."""

from __future__ import annotations

import dataclasses
import json

import pytest

from helpers import UTC, t_of
from robot_autonomy import schema
from robot_autonomy.schema import Block, Intervention


def test_constants_are_the_spec_values():
    assert schema.SCHEMA == "robot-autonomy/1"
    assert schema.LEVELS == ("hands_on", "remote")
    assert schema.READINGS == ("asked", "observed")
    assert schema.AVOIDABLE == ("yes", "no", "unclear")
    assert schema.DEFAULT_CAUSES == {
        "hands_on": (
            "rehome_or_reposition_arm",
            "power_or_replug",
            "belt_or_items",
            "clear_workcell",
            "lights_or_cameras",
            "mechanical",
            "inspect",
            "other_hands_on",
        ),
        "remote": ("run_robot_command", "approve_robot_run", "answer_robot_question", "other_remote"),
    }
    assert schema.COMMISSIONING_CAUSES == ("mechanical",)
    assert schema.SOURCE_READING == {
        "chat": "asked",
        "agent": "asked",
        "queue": "asked",
        "operator": "observed",
        "human": "observed",
        "field_service": "observed",
        "robot": "observed",
        "diagnostics": "observed",
    }


def test_day_of_uses_the_given_timezone():
    last_second_of_day_utc = t_of("2026-11-18", 24.0) - 1.0
    assert schema.day_of(last_second_of_day_utc, UTC) == "2026-11-18"
    assert schema.day_of(t_of("2026-11-19"), UTC) == "2026-11-19"
    # 05:00 UTC on 2026-11-18 is 21:00 PST (UTC-8) on 2026-11-17 in Vancouver, 14:00 JST (UTC+9) in Tokyo.
    five_am_utc = t_of("2026-11-18", 5.0)
    assert schema.day_of(five_am_utc, "America/Vancouver") == "2026-11-17"
    assert schema.day_of(five_am_utc, "Asia/Tokyo") == "2026-11-18"
    # 20:00 UTC is 05:00 the next day in Tokyo.
    assert schema.day_of(t_of("2026-11-18", 20.0), "Asia/Tokyo") == "2026-11-19"


@pytest.mark.parametrize(
    ("source", "reading"),
    [
        ("chat", "asked"),
        ("agent", "asked"),
        ("queue", "asked"),
        ("operator", "observed"),
        ("human", "observed"),
        ("field_service", "observed"),
        ("robot", "observed"),
        ("diagnostics", "observed"),
        ("slack_bot", "asked"),  # any other source -> "asked"
    ],
)
def test_reading_resolves_from_the_source(source, reading):
    intervention = Intervention(id="iv-1", t=1.0, robot="arm-1", level="hands_on", cause="inspect", source=source)
    assert intervention.reading == reading


def test_an_explicit_reading_wins_over_the_source():
    intervention = Intervention(
        id="iv-1", t=1.0, robot="arm-1", level="remote", cause="other_remote", source="chat", reading="observed"
    )
    assert intervention.reading == "observed"


@pytest.mark.parametrize(
    "bad_fields",
    [
        {"level": "sideways"},
        {"level": "none"},
        {"cause": ""},
        {"reading": "maybe"},
        {"avoidable": "perhaps"},
    ],
)
def test_invalid_fields_raise_value_error(bad_fields):
    fields = {"id": "iv-1", "t": 1.0, "robot": "arm-1", "level": "hands_on", "cause": "inspect", "source": "chat"}
    fields.update(bad_fields)
    with pytest.raises(ValueError):
        Intervention(**fields)


def test_commissioning_resolves_from_the_cause():
    mechanical = Intervention(id="a", t=1.0, robot="arm-1", level="hands_on", cause="mechanical", source="chat")
    power = Intervention(id="b", t=1.0, robot="arm-1", level="hands_on", cause="power_or_replug", source="chat")
    explicit_false = Intervention(
        id="c", t=1.0, robot="arm-1", level="hands_on", cause="mechanical", source="chat", commissioning=False
    )
    explicit_true = Intervention(
        id="d", t=1.0, robot="arm-1", level="hands_on", cause="inspect", source="chat", commissioning=True
    )
    assert mechanical.commissioning is True
    assert power.commissioning is False
    assert explicit_false.commissioning is False
    assert explicit_true.commissioning is True


def test_defaults():
    intervention = Intervention(id="iv-1", t=1.0, robot="arm-1", level="hands_on", cause="inspect", source="chat")
    assert intervention.what == ""
    assert intervention.avoidable == "unclear"
    assert intervention.fix == ""
    assert intervention.thread is None
    assert intervention.by is None
    assert intervention.human_s is None
    assert intervention.downtime_s is None
    assert intervention.evidence is None


def test_times_are_floats():
    intervention = Intervention(id="iv-1", t=100, robot="arm-1", level="hands_on", cause="inspect", source="chat")
    assert isinstance(intervention.t, float)
    assert intervention.t == 100.0
    block = Block(id="b1", robot="arm-1", cause="approve_robot_run", why="go?", since_t=100, until_t=160)
    assert isinstance(block.since_t, float)
    assert isinstance(block.until_t, float)


def test_intervention_is_frozen():
    intervention = Intervention(id="iv-1", t=1.0, robot="arm-1", level="hands_on", cause="inspect", source="chat")
    with pytest.raises(dataclasses.FrozenInstanceError):
        intervention.cause = "other"  # type: ignore[misc]


def test_to_dict_adds_schema_stores_resolved_fields_and_omits_none():
    intervention = Intervention(
        id="iv-1", t=1.5, robot="arm-1", level="hands_on", cause="mechanical", source="operator", what="tightened"
    )
    as_dict = intervention.to_dict()
    assert as_dict["schema"] == "robot-autonomy/1"
    assert as_dict["id"] == "iv-1"
    assert as_dict["t"] == 1.5
    assert as_dict["reading"] == "observed"  # stored resolved
    assert as_dict["commissioning"] is True  # stored resolved
    assert as_dict["what"] == "tightened"
    assert as_dict["avoidable"] == "unclear"
    assert as_dict["fix"] == ""  # only None values are omitted
    for omitted in ("thread", "by", "human_s", "downtime_s", "evidence"):
        assert omitted not in as_dict
    assert None not in as_dict.values()
    json.dumps(as_dict)


def test_from_dict_round_trips_and_ignores_unknown_keys():
    intervention = Intervention(
        id="iv-7",
        t=1234.25,
        robot="arm-2",
        level="remote",
        cause="approve_robot_run",
        source="chat",
        what="give the go",
        avoidable="yes",
        fix="auto-approve sim-passed runs",
        thread="chat-42",
        by="pick agent",
        human_s=45.0,
        downtime_s=600.0,
        evidence="transcript#L12",
    )
    as_dict = intervention.to_dict()
    assert Intervention.from_dict(as_dict) == intervention
    assert Intervention.from_dict(as_dict).to_dict() == as_dict
    with_extras = dict(as_dict, unknown_key="ignored", another={"nested": 1})
    assert Intervention.from_dict(with_extras) == intervention
    # Through JSON text too (the ledger's path).
    assert Intervention.from_dict(json.loads(json.dumps(as_dict))) == intervention


def test_block_seconds():
    open_block = Block(id="b1", robot="arm-1", cause="approve_robot_run", why="go?", since_t=100.0)
    assert open_block.until_t is None
    assert open_block.seconds(160.0) == pytest.approx(60.0)
    assert open_block.seconds(40.0) == 0.0  # max(0, ...)
    closed_block = Block(id="b2", robot="arm-1", cause="approve_robot_run", why="go?", since_t=100.0, until_t=130.0)
    assert closed_block.seconds(1000.0) == pytest.approx(30.0)


def test_block_defaults_and_validation():
    block = Block(id="b1", robot="arm-1", cause="approve_robot_run", why="go?", since_t=1.0)
    assert block.level == "remote"
    assert block.source == "queue"
    assert block.item is None
    assert block.by is None
    with pytest.raises(ValueError):
        Block(id="b1", robot="arm-1", cause="approve_robot_run", why="go?", since_t=1.0, level="sideways")


def test_block_to_dict_and_from_dict():
    block = Block(
        id="q1@1100.5",
        robot="arm-1",
        cause="answer_robot_question",
        why="which bin?",
        since_t=1100.5,
        level="remote",
        source="queue",
        item="q1",
        by="pick agent",
    )
    as_dict = block.to_dict()
    assert as_dict["schema"] == "robot-autonomy/1"
    assert "until_t" not in as_dict  # None omitted
    assert as_dict["item"] == "q1"
    assert Block.from_dict(as_dict) == block
    assert Block.from_dict(dict(as_dict, extra="ignored")) == block
    closed = Block.from_dict(dict(as_dict, until_t=1400.5))
    assert closed.until_t == 1400.5
    assert Block.from_dict(closed.to_dict()) == closed
