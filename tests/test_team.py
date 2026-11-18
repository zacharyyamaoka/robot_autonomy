"""team.py: the AI Team directory's status card (SPEC "team.py")."""

from __future__ import annotations

import json

import pytest

from helpers import DAY_S, day_range, hours_table, make_episode, t_of
from robot_autonomy import metrics, team
from robot_autonomy.agent import AgentConfig
from robot_autonomy.backlog import Backlog

NOW = t_of("2026-11-18", 6.0)
HOURS = hours_table("arm-1", day_range("2026-11-11", "2026-11-17"), 10.0)


def _kpi():
    # 70 h, 2 counted asked episodes -> 35 h (R3); observed 0 -> 70 h (R3): held R3 "Overnight", next R4 at 168 h
    episodes = [make_episode("a", t_of("2026-11-12", 9.0)), make_episode("b", t_of("2026-11-15", 9.0))]
    return metrics.kpi(episodes, HOURS, end="2026-11-17", days=7, tz="UTC")


def _config(**overrides):
    defaults = {"tz": "UTC", "links": {"report": "/abs/reports/autonomy.html", "ledger": "/abs/autonomy"}}
    defaults.update(overrides)
    return AgentConfig(**defaults)


def _item(board, title, status_path, proposed_t, done_t=None, causes=("power_or_replug",)):
    item = board.propose(title=title, causes=causes, owner="dev agent", proposed_by="Robot Autonomy Agent",
                         now=proposed_t)
    for status in status_path:
        if status == "done":
            board.mark_done(item.id, by="dev agent", evidence=("abc1234",), now=done_t)
        elif status in ("verified", "ineffective"):
            board.record_effect(item.id, {"verdict": status}, status, by="Robot Autonomy Agent", now=done_t + 60.0)
        else:
            board.move(item.id, status, by="dev agent", now=proposed_t + 1.0)
    return item


@pytest.fixture
def busy_backlog(autonomy_root):
    board = Backlog(autonomy_root)
    _item(board, "P1 proposed", (), NOW - 9 * DAY_S)
    _item(board, "P2 accepted", ("accepted",), NOW - 8 * DAY_S)
    _item(board, "P3 doing", ("doing",), NOW - 7 * DAY_S)
    _item(board, "P4 proposed", (), NOW - 6 * DAY_S)
    _item(board, "P5 doing", ("doing",), NOW - 5 * DAY_S)
    _item(board, "D1 done recently", ("doing", "done"), NOW - 20 * DAY_S, done_t=NOW - 2 * DAY_S)
    _item(board, "D2 done long ago", ("doing", "done"), NOW - 30 * DAY_S, done_t=NOW - 10 * DAY_S)
    _item(board, "V1 verified", ("doing", "done", "verified"), NOW - 20 * DAY_S, done_t=NOW - 3 * DAY_S)
    _item(board, "I1 ineffective", ("doing", "done", "ineffective"), NOW - 40 * DAY_S, done_t=NOW - 20 * DAY_S)
    _item(board, "X1 dropped", ("dropped",), NOW - 4 * DAY_S)
    return board


def test_team_schema():
    assert team.TEAM_SCHEMA == "ai-team-status/1"


def test_card_shape(busy_backlog):
    config = _config()
    card = team.status_card(_kpi(), busy_backlog, config=config, now=NOW)
    assert card["schema"] == "ai-team-status/1"
    assert card["member"] == "Robot Autonomy Agent"
    assert card["team"] == "Robot Team"
    assert card["remit"] == config.remit
    assert card["kpi"] == {
        "name": "working hours between hands-on interventions (7 d)",
        "value": pytest.approx(35.0),
        "unit": "h",
        "direction": "up",
        "target": pytest.approx(168.0),
        "level": "R3",
        "level_name": "Overnight",
        "next_gate": "R4",
        "on_track": False,  # no growth given
    }
    assert card["counter_kpi"] == {
        "name": "working hours (7 d)",
        "value": pytest.approx(70.0),
        "unit": "h",
        "owner": "Robot Activity Agent",
    }
    assert card["links"] == config.links
    assert card["updated_t"] == NOW
    json.dumps(card)


def test_todo_counts_and_top(busy_backlog):
    card = team.status_card(_kpi(), busy_backlog, config=_config(), now=NOW)
    todo = card["todo"]
    assert todo["open"] == 5  # P1 P2 P3 P4 P5
    assert todo["done_7d"] == 2  # done_t in the last 7 days: D1 (2 d ago), V1 (3 d ago)
    assert todo["verified"] == 1  # V1
    assert todo["top"] == ["P3 doing", "P5 doing", "P1 proposed"]  # doing first, then by proposed_t; at most 3


def test_status_without_a_claim(busy_backlog, tmp_path):
    card = team.status_card(_kpi(), busy_backlog, config=_config(), now=NOW)
    assert card["status"] == "working"  # an item is in "doing"
    assert card["blocked_on"] is None
    quiet = Backlog(tmp_path / "quiet")
    _item(quiet, "only proposed", (), NOW - DAY_S)
    card = team.status_card(_kpi(), quiet, config=_config(), now=NOW)
    assert card["status"] == "idle"
    assert card["blocked_on"] is None
    assert card["todo"]["top"] == ["only proposed"]


@pytest.mark.parametrize("status", ["waiting_on_zach", "blocked"])
def test_claim_overrides_status_and_needs_blocked_on(busy_backlog, status):
    card = team.status_card(_kpi(), busy_backlog, config=_config(), now=NOW,
                            claim={"status": status, "blocked_on": "a W&B sign-in"})
    assert card["status"] == status
    assert card["blocked_on"] == "a W&B sign-in"
    with pytest.raises(ValueError):
        team.status_card(_kpi(), busy_backlog, config=_config(), now=NOW, claim={"status": status})
    with pytest.raises(ValueError):
        team.status_card(_kpi(), busy_backlog, config=_config(), now=NOW, claim={"status": status, "blocked_on": None})


def test_claim_idle_overrides_doing(busy_backlog):
    card = team.status_card(_kpi(), busy_backlog, config=_config(), now=NOW, claim={"status": "idle"})
    assert card["status"] == "idle"
    assert card["blocked_on"] is None


def test_kpi_never_comes_from_the_claim(busy_backlog):
    claim = {"status": "working", "blocked_on": None, "kpi": {"value": 999.0, "level": "R4"}, "value": 999.0}
    card = team.status_card(_kpi(), busy_backlog, config=_config(), now=NOW, claim=claim)
    assert card["kpi"]["value"] == pytest.approx(35.0)
    assert card["kpi"]["level"] == "R3"


def test_on_track_follows_growth(busy_backlog):
    improving = metrics.growth([2.0, 8.0], 16.0)  # beta ~= 0.72
    worsening = metrics.growth([15.0, 16.0], 16.0)  # beta ~= 31
    assert team.status_card(_kpi(), busy_backlog, config=_config(), now=NOW, growth=improving)["kpi"]["on_track"] is True
    assert (
        team.status_card(_kpi(), busy_backlog, config=_config(), now=NOW, growth=worsening)["kpi"]["on_track"] is False
    )
    assert team.status_card(_kpi(), busy_backlog, config=_config(), now=NOW, growth=None)["kpi"]["on_track"] is False


def test_card_without_hours(busy_backlog):
    no_hours = metrics.kpi([], {}, end="2026-11-17", days=7, tz="UTC")
    card = team.status_card(no_hours, busy_backlog, config=_config(), now=NOW)
    assert card["kpi"]["value"] is None
    assert card["kpi"]["level"] == "R0"
    assert card["kpi"]["next_gate"] == "R1"
    assert card["kpi"]["target"] == pytest.approx(1.0)
    assert card["counter_kpi"]["value"] is None


def test_member_and_owner_follow_the_config(busy_backlog):
    card = team.status_card(
        _kpi(), busy_backlog, config=_config(name="Autonomy (cell 2)", activity_agent="Cell 2 Activity"), now=NOW
    )
    assert card["member"] == "Autonomy (cell 2)"
    assert card["counter_kpi"]["owner"] == "Cell 2 Activity"
