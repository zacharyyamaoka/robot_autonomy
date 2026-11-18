"""agent.py: the agent's one loop (SPEC "agent.py — the agent's one loop")."""

from __future__ import annotations

import json
import math

import pytest

from helpers import (
    DAY_S,
    HOUR_S,
    day_range,
    file_lines,
    hours_table,
    make_intervention,
    read_jsonl,
    snapshot_tree,
    t_of,
    write_jsonl,
)
from robot_autonomy import verify
from robot_autonomy.agent import Action, AgentConfig, AutonomyAgent, load_config
from robot_autonomy.backlog import Backlog
from robot_autonomy.episodes import episodes as to_episodes
from robot_autonomy.ingest import SourceConfig
from robot_autonomy.ledger import Ledger
from robot_autonomy.schema import Block

NOW = t_of("2026-11-18", 6.0)  # the KPI window ends 2026-11-17
OWNER = "Robot Autonomy Agent"


def config(**overrides) -> AgentConfig:
    return AgentConfig(tz="UTC", **overrides)


def agent(root, *, hours, notifier=None, **config_overrides) -> AutonomyAgent:
    return AutonomyAgent(root, config=config(**config_overrides), notifier=notifier, hours=hours)


def kinds(actions) -> list[str]:
    return [action.kind for action in actions]


def test_agent_config_defaults():
    defaults = AgentConfig()
    assert defaults.name == "Robot Autonomy Agent"
    assert defaults.owner == "Robot Autonomy Agent"
    assert defaults.dispatcher == "dispatcher"
    assert defaults.activity_agent == "Robot Activity Agent"
    assert tuple(defaults.count_levels) == ("hands_on",)
    assert defaults.window_days == 7
    assert defaults.top_causes == 3
    assert defaults.min_episodes == 3
    assert defaults.block_alert_s == 1800.0
    assert defaults.stale_days == 7.0
    assert defaults.remind_s == 86400.0
    assert defaults.baseline_days == 28
    assert defaults.min_after_h == 24.0
    assert defaults.alpha == 0.1
    assert defaults.tz is None
    assert defaults.hours is None
    assert tuple(defaults.sources) == ()
    assert defaults.status_path is None
    assert defaults.remit == (
        "Find every reason a person had to step in, from every source, and drive each reason to zero."
    )
    assert defaults.links == {}


# --------------------------------------------------------------------------------------------- propose

PROPOSAL_HOURS = hours_table("arm-1", day_range("2026-11-01", "2026-11-17"), 10.0)


def _record(ledger, cause, day, hours_of_day, avoidables, fixes=None, level="hands_on"):
    for index, hour in enumerate(hours_of_day):
        ledger.record(
            make_intervention(
                f"{cause}-{day}-{hour}",
                t_of(day, hour),
                level=level,
                cause=cause,
                avoidable=avoidables[index],
                fix=(fixes[index] if fixes else ""),
            )
        )


@pytest.fixture
def proposal_root(autonomy_root):
    """Every episode is its own (no threads), all within the 7 days before NOW, all hands_on, source chat.

    backlog_order(causes(...)) =
      mechanical       6 episodes, 6 avoidable  -> commissioning: skipped
      power_or_replug  5 episodes, 5 avoidable  fixes: powered hub x3, strain relief x2
      belt_or_items    4 episodes, 3 avoidable  fixes: belt guard
      lights_or_cameras 3 episodes, 2 avoidable no fixes
      clear_workcell   2 episodes, 2 avoidable  -> fewer than min_episodes 3: skipped
      (inspect: 4 episodes, 0 avoidable -> not in backlog_order)
    """
    ledger = Ledger(autonomy_root)
    _record(ledger, "power_or_replug", "2026-11-12", (0, 3, 6, 9, 12), ["yes"] * 5,
            ["powered hub", "powered hub", "strain relief", "powered hub", "strain relief"])
    _record(ledger, "belt_or_items", "2026-11-13", (1, 4, 7, 10), ["yes", "yes", "yes", "no"], ["belt guard"] * 4)
    _record(ledger, "lights_or_cameras", "2026-11-14", (2, 5, 8), ["yes", "yes", "unclear"])
    _record(ledger, "clear_workcell", "2026-11-15", (1, 2), ["yes", "yes"])
    _record(ledger, "mechanical", "2026-11-16", (1, 2, 3, 4, 5, 6), ["yes"] * 6, ["bolt it down"] * 6)
    _record(ledger, "inspect", "2026-11-17", (1, 2, 3, 4), ["no"] * 4)
    return autonomy_root


def test_tick_proposes_for_the_top_avoidable_causes(proposal_root, notifier):
    actions = agent(proposal_root, hours=PROPOSAL_HOURS, notifier=notifier).tick(NOW)
    assert kinds(actions) == ["propose", "propose", "propose"]
    assert [action.cause for action in actions] == ["power_or_replug", "belt_or_items", "lights_or_cameras"]
    items = {item.causes[0]: item for item in Backlog(proposal_root).items()}
    assert set(items) == {"power_or_replug", "belt_or_items", "lights_or_cameras"}

    power = items["power_or_replug"]
    assert power.title == "Remove power_or_replug: powered hub"
    assert power.causes == ("power_or_replug",)
    assert power.owner == OWNER
    assert power.proposed_by == "Robot Autonomy Agent"
    assert power.status == "proposed"
    assert power.why == "5 episodes, 5 avoidable, 5 in the last 7 days"
    assert power.plan == "powered hub; strain relief"
    assert power.expected == (
        "power_or_replug per 100 working hours falls; verified by an exact rate test after 24 working hours"
    )
    assert power.proposed_t == NOW

    belt = items["belt_or_items"]
    assert belt.title == "Remove belt_or_items: belt guard"
    assert belt.why == "4 episodes, 3 avoidable, 4 in the last 7 days"
    assert belt.plan == "belt guard"

    lights = items["lights_or_cameras"]
    assert lights.title == "Remove lights_or_cameras"
    assert lights.why == "3 episodes, 2 avoidable, 3 in the last 7 days"
    assert lights.plan == ""

    for action in actions:
        assert isinstance(action, Action)
        item = items[action.cause]
        assert tuple(action.to) == (OWNER, "dispatcher")
        assert action.improvement == item.id
        assert action.key == f"propose:{item.id}"
        assert action.message


def test_min_after_h_in_the_expected_line(proposal_root):
    agent(proposal_root, hours=PROPOSAL_HOURS, min_after_h=12.5, top_causes=1).tick(NOW)
    [item] = Backlog(proposal_root).items()
    assert item.expected == (
        "power_or_replug per 100 working hours falls; verified by an exact rate test after 12.5 working hours"
    )


def test_tick_does_not_propose_twice(proposal_root):
    autonomy = agent(proposal_root, hours=PROPOSAL_HOURS)
    autonomy.tick(NOW)
    assert "propose" not in kinds(autonomy.tick(NOW + HOUR_S))
    # a fresh agent object on the same root sees the live improvements too
    assert "propose" not in kinds(agent(proposal_root, hours=PROPOSAL_HOURS).tick(NOW + 2 * HOUR_S))
    assert len(Backlog(proposal_root).items()) == 3


@pytest.mark.parametrize(
    ("overrides", "expected_causes"),
    [
        ({"top_causes": 2}, ["power_or_replug", "belt_or_items"]),
        ({"top_causes": 1}, ["power_or_replug"]),
        ({"min_episodes": 5}, ["power_or_replug"]),
        ({"min_episodes": 4}, ["power_or_replug", "belt_or_items"]),
        ({"min_episodes": 2}, ["power_or_replug", "belt_or_items", "lights_or_cameras"]),
        ({"min_episodes": 2, "top_causes": 4},
         ["power_or_replug", "belt_or_items", "lights_or_cameras", "clear_workcell"]),
        ({"top_causes": 10}, ["power_or_replug", "belt_or_items", "lights_or_cameras"]),
    ],
)
def test_top_causes_and_min_episodes(proposal_root, overrides, expected_causes):
    actions = agent(proposal_root, hours=PROPOSAL_HOURS, **overrides).tick(NOW)
    assert [action.cause for action in actions if action.kind == "propose"] == expected_causes


def test_an_existing_live_improvement_counts_toward_top_causes(proposal_root):
    board = Backlog(proposal_root)
    existing = board.propose(title="Belt guard v2", causes=("belt_or_items",), owner="dev agent", proposed_by="Zach",
                             now=NOW - HOUR_S)
    board.move(existing.id, "doing", by="dev agent", now=NOW - HOUR_S)
    actions = agent(proposal_root, hours=PROPOSAL_HOURS).tick(NOW)
    assert [action.cause for action in actions if action.kind == "propose"] == ["power_or_replug", "lights_or_cameras"]
    assert len(Backlog(proposal_root).items(cause="belt_or_items")) == 1


def test_an_existing_live_improvement_can_fill_the_top(proposal_root):
    board = Backlog(proposal_root)
    board.propose(title="Belt guard v2", causes=("belt_or_items",), owner="dev agent", proposed_by="Zach",
                  now=NOW - HOUR_S)
    actions = agent(proposal_root, hours=PROPOSAL_HOURS, top_causes=2).tick(NOW)
    assert [action.cause for action in actions if action.kind == "propose"] == ["power_or_replug"]


def _verified_power_fix(root, done_t):
    board = Backlog(root)
    item = board.propose(title="Old hub fix", causes=("power_or_replug",), owner="dev agent", proposed_by="Zach",
                         now=t_of("2026-11-11"))
    board.move(item.id, "doing", by="dev agent", now=t_of("2026-11-11", 1.0))
    board.mark_done(item.id, by="dev agent", evidence=("abc1234",), now=done_t)
    board.record_effect(item.id, {"verdict": "verified"}, "verified", by="Robot Autonomy Agent", now=done_t + 1.0)
    return item


def test_only_episodes_after_a_verified_fix_are_live(proposal_root):
    # power episodes at 00, 03, 06, 09, 12 h on 2026-11-12; a fix verified with done_t at 07:00 leaves 2 live
    _verified_power_fix(proposal_root, t_of("2026-11-12", 7.0))
    actions = agent(proposal_root, hours=PROPOSAL_HOURS).tick(NOW)
    assert [action.cause for action in actions if action.kind == "propose"] == ["belt_or_items", "lights_or_cameras"]


def test_live_episodes_set_the_proposal_text(proposal_root):
    # done_t at 04:00 leaves 3 live power episodes: 06 h (strain relief), 09 h (powered hub), 12 h (strain relief)
    _verified_power_fix(proposal_root, t_of("2026-11-12", 4.0))
    actions = agent(proposal_root, hours=PROPOSAL_HOURS).tick(NOW)
    # live power now has 3 episodes, 3 avoidable: belt (3 avoidable, 4 episodes) sorts ahead of it
    assert [action.cause for action in actions if action.kind == "propose"] == [
        "belt_or_items", "power_or_replug", "lights_or_cameras"
    ]
    [new_power] = [item for item in Backlog(proposal_root).items(status="proposed")
                   if item.causes == ("power_or_replug",)]
    assert new_power.title == "Remove power_or_replug: strain relief"
    assert new_power.why == "3 episodes, 3 avoidable, 3 in the last 7 days"
    assert new_power.plan == "strain relief; powered hub"


def test_recipients_are_deduplicated_in_order(proposal_root):
    actions = agent(proposal_root, hours=PROPOSAL_HOURS, owner="dispatcher", top_causes=1).tick(NOW)
    [proposal] = actions
    assert tuple(proposal.to) == ("dispatcher",)
    [item] = Backlog(proposal_root).items()
    assert item.owner == "dispatcher"


# --------------------------------------------------------------------------------------------- KPI row and levels

LEVEL_HOURS = hours_table("arm-1", day_range("2026-11-01", "2026-11-30"), 10.0)


def _record_batch(root, day):
    """10 hands_on asks (source chat, cause inspect, avoidable no: never a proposal) on `day` at 01:00..10:00."""
    ledger = Ledger(root)
    for hour in range(1, 11):
        ledger.record(make_intervention(f"inspect-{day}-{hour}", t_of(day, hour), cause="inspect", avoidable="no"))


def test_kpi_row_is_written_once_per_end_day(autonomy_root):
    _record_batch(autonomy_root, "2026-11-11")
    autonomy = agent(autonomy_root, hours=LEVEL_HOURS)
    actions = autonomy.tick(NOW)
    assert actions == []  # no previous row: no level action; nothing avoidable; no blocks; nothing stale
    [row] = read_jsonl(autonomy_root / "agent" / "kpi.jsonl")
    # window 2026-11-11 .. 2026-11-17: 70 h; 10 counted asked episodes -> 7 h (R1); observed 0 -> 70 h
    assert row["t"] == NOW
    assert row["end"] == "2026-11-17"
    assert row["level"] == "R1"
    assert row["mtbi_h"] == pytest.approx(7.0)
    assert row["working_h"] == pytest.approx(70.0)
    assert row["counted"] == 10
    assert row["readings"] == {"asked": pytest.approx(7.0), "observed": pytest.approx(70.0)}
    assert row["human_s_per_h"] == pytest.approx(10 * 300.0 / 70.0)
    assert row["blocked_h"] == pytest.approx(0.0)
    assert "growth" in row
    assert row["growth"] is None or {"beta", "mtbi_now_h"} <= set(row["growth"])
    # a second tick the same day: no new row
    autonomy.tick(NOW + HOUR_S)
    agent(autonomy_root, hours=LEVEL_HOURS).tick(NOW + 2 * HOUR_S)
    assert len(read_jsonl(autonomy_root / "agent" / "kpi.jsonl")) == 1


def test_agent_kpi_is_the_window_ending_yesterday(autonomy_root):
    _record_batch(autonomy_root, "2026-11-11")
    result = agent(autonomy_root, hours=LEVEL_HOURS).kpi(NOW)
    assert result.start == "2026-11-11"
    assert result.end == "2026-11-17"
    assert result.working_h == pytest.approx(70.0)
    assert result.level == "R1"


def test_level_up_and_level_down(autonomy_root, notifier):
    _record_batch(autonomy_root, "2026-11-11")
    autonomy = agent(autonomy_root, hours=LEVEL_HOURS, notifier=notifier)
    assert autonomy.tick(NOW) == []  # R1
    # window 2026-11-12 .. 2026-11-18: no episodes -> 70 h -> R3
    [level_up] = autonomy.tick(NOW + DAY_S)
    assert level_up.kind == "level_up"
    assert tuple(level_up.to) == ("dispatcher",)
    assert level_up.key == "level:2026-11-18"
    _record_batch(autonomy_root, "2026-11-19")
    # window 2026-11-13 .. 2026-11-19: 10 episodes -> 7 h -> R1
    [level_down] = autonomy.tick(NOW + 2 * DAY_S)
    assert level_down.kind == "level_down"
    assert tuple(level_down.to) == ("dispatcher",)
    assert level_down.key == "level:2026-11-19"
    rows = read_jsonl(autonomy_root / "agent" / "kpi.jsonl")
    assert [(row["end"], row["level"]) for row in rows] == [
        ("2026-11-17", "R1"), ("2026-11-18", "R3"), ("2026-11-19", "R1")
    ]
    assert [action.kind for action in notifier.delivered] == ["level_up", "level_down"]


def test_hours_from_the_config(autonomy_root, tmp_path):
    hours_file = write_jsonl(
        tmp_path / "hours.jsonl",
        [{"robot": "arm-1", "day": day, "hours": 10.0} for day in day_range("2026-11-11", "2026-11-17")],
    )
    _record_batch(autonomy_root, "2026-11-11")
    autonomy = AutonomyAgent(autonomy_root, config=config(hours={"kind": "jsonl", "path": str(hours_file)}))
    assert autonomy.kpi(NOW).working_h == pytest.approx(70.0)
    assert autonomy.kpi(NOW).mtbi_h == pytest.approx(7.0)


def test_hours_from_robot_activity_samples(autonomy_root, tmp_path):
    activity = tmp_path / "activity"
    for day in day_range("2026-11-11", "2026-11-17"):
        # one activity row (claimed, an item, health ok) weighing 10 h, and one dead row weighing 5 h
        write_jsonl(activity / "samples" / "arm-1" / f"{day}.jsonl", [
            {"schema": "robot-activity/1", "t": t_of(day, 12.0), "robot": "arm-1", "sample_s": 36000.0,
             "claimed_by": "pick agent", "item": "q1", "health": "ok"},
            {"schema": "robot-activity/1", "t": t_of(day, 13.0), "robot": "arm-1", "sample_s": 18000.0,
             "claimed_by": None, "item": None, "health": "ok"},
        ])
    _record_batch(autonomy_root, "2026-11-11")
    by_activity = AutonomyAgent(
        autonomy_root, config=config(hours={"kind": "robot_activity", "root": str(activity), "basis": "activity"})
    )
    assert by_activity.kpi(NOW).working_h == pytest.approx(70.0)
    by_tracked = AutonomyAgent(
        autonomy_root, config=config(hours={"kind": "robot_activity", "root": str(activity), "basis": "tracked"})
    )
    assert by_tracked.kpi(NOW).working_h == pytest.approx(105.0)


# --------------------------------------------------------------------------------------------- verify

VERIFY_HOURS = hours_table("arm-1", day_range("2026-10-01", "2026-11-30"), 10.0)
DONE_T = t_of("2026-11-10", 14.0)
# baseline_days=7: before = 2026-11-03 .. 2026-11-09 (70 h); after = 2026-11-11 .. the day before `now`


def _done_fix(root, owner="dev agent"):
    board = Backlog(root)
    item = board.propose(title="Add a powered hub", causes=("power_or_replug",), owner=owner,
                         proposed_by="Robot Autonomy Agent", now=t_of("2026-11-01"))
    board.move(item.id, "doing", by=owner, now=t_of("2026-11-02"))
    board.mark_done(item.id, by=owner, evidence=("abc1234",), now=DONE_T)
    return item


def _record_power(root, day_hours):
    ledger = Ledger(root)
    for day, hour in day_hours:
        ledger.record(make_intervention(f"power-{day}-{hour}", t_of(day, hour), avoidable="no"))


BEFORE_9 = [("2026-11-03", 1), ("2026-11-03", 2), ("2026-11-04", 1), ("2026-11-05", 1), ("2026-11-06", 1),
            ("2026-11-06", 2), ("2026-11-07", 1), ("2026-11-08", 1), ("2026-11-09", 1)]


def test_verify_marks_a_done_fix_verified(autonomy_root, notifier):
    item = _done_fix(autonomy_root)
    _record_power(autonomy_root, BEFORE_9)  # 9 before, 0 after
    now = t_of("2026-11-14", 6.0)  # after = 11-11 .. 11-13 = 30 h; p = (70 / 100) ** 9 ~= 0.040 < 0.1
    actions = agent(autonomy_root, hours=VERIFY_HOURS, notifier=notifier, baseline_days=7).tick(now)
    assert kinds(actions) == ["verified"]
    [action] = actions
    assert tuple(action.to) == ("dev agent", "dispatcher")
    assert action.improvement == item.id
    assert action.key == f"verified:{item.id}"
    assert action.message
    verified = Backlog(autonomy_root).get(item.id)
    assert verified.status == "verified"
    assert verified.verified_t == now
    expected = verify.assess(
        ("power_or_replug",), DONE_T, to_episodes(Ledger(autonomy_root).interventions()), VERIFY_HOURS, now=now,
        baseline_days=7, min_after_h=24.0, alpha=0.1, tz="UTC",
    )
    assert expected.verdict == "verified"
    assert expected.p_value == pytest.approx(0.7**9)
    assert verified.effect == json.loads(json.dumps(expected.to_dict()))
    assert [delivered.key for delivered in notifier.delivered] == [action.key]


def test_verify_marks_a_done_fix_ineffective(autonomy_root):
    item = _done_fix(autonomy_root)
    # before 8 in 70 h (11.4 / 100 h); after 4 in 30 h (13.3 / 100 h): p = P(X <= 4 | Bin(12, 0.3)) ~= 0.72;
    # after >= before and expected after = 11.43 * 30 / 100 = 3.4 >= 3
    _record_power(autonomy_root, BEFORE_9[:8] + [("2026-11-11", 1), ("2026-11-11", 2), ("2026-11-12", 1),
                                                 ("2026-11-13", 1)])
    now = t_of("2026-11-14", 6.0)
    p_value = sum(math.comb(12, k) * 0.3**k * 0.7 ** (12 - k) for k in range(5))
    assert p_value > 0.1
    actions = agent(autonomy_root, hours=VERIFY_HOURS, baseline_days=7).tick(now)
    assert kinds(actions) == ["ineffective"]
    assert tuple(actions[0].to) == ("dev agent", "dispatcher")
    assert actions[0].key == f"ineffective:{item.id}"
    after = Backlog(autonomy_root).get(item.id)
    assert after.status == "ineffective"
    assert after.verified_t == now
    assert isinstance(after.effect, dict)


def test_verify_leaves_a_fix_done_while_pending(autonomy_root):
    item = _done_fix(autonomy_root)
    _record_power(autonomy_root, BEFORE_9)
    now = t_of("2026-11-12", 6.0)  # after = 11-11 only: 10 h < min_after_h 24
    actions = agent(autonomy_root, hours=VERIFY_HOURS, baseline_days=7).tick(now)
    assert actions == []
    still = Backlog(autonomy_root).get(item.id)
    assert still.status == "done"
    assert still.effect is None
    assert still.verified_t is None


# --------------------------------------------------------------------------------------------- blocks

def _open_blocks(root):
    ledger = Ledger(root)
    ledger.open_block(Block(id="b-old", robot="arm-1", cause="approve_robot_run", why="approve the pick run",
                            since_t=NOW - HOUR_S, item="q7", by="pick agent"), count=False)
    ledger.open_block(Block(id="b-anon", robot="arm-2", cause="answer_robot_question", why="which bin?",
                            since_t=NOW - 2 * HOUR_S, item="q8"), count=False)
    ledger.open_block(Block(id="b-young", robot="arm-1", cause="approve_robot_run", why="go?",
                            since_t=NOW - 600.0, item="q9", by="pick agent"), count=False)
    ledger.open_block(Block(id="b-closed", robot="arm-1", cause="approve_robot_run", why="go?",
                            since_t=NOW - 5 * HOUR_S, item="q6", by="pick agent"), count=False)
    ledger.close_block("b-closed", until_t=NOW - 4 * HOUR_S)


def test_blocked_alerts(autonomy_root):
    _open_blocks(autonomy_root)
    actions = agent(autonomy_root, hours={}).tick(NOW)
    assert kinds(actions) == ["blocked", "blocked"]
    by_key = {action.key: action for action in actions}
    assert set(by_key) == {"blocked:b-old", "blocked:b-anon"}
    old = by_key["blocked:b-old"]
    assert tuple(old.to) == ("pick agent", "Robot Activity Agent", "dispatcher")
    assert old.robot == "arm-1"
    assert "arm-1" in old.message
    assert "q7" in old.message
    assert "approve the pick run" in old.message
    anonymous = by_key["blocked:b-anon"]
    assert tuple(anonymous.to) == (OWNER, "Robot Activity Agent", "dispatcher")  # block.by is None
    assert anonymous.robot == "arm-2"


def test_block_alert_threshold_is_configurable(autonomy_root):
    _open_blocks(autonomy_root)
    actions = agent(autonomy_root, hours={}, block_alert_s=300.0).tick(NOW)
    assert {action.key for action in actions} == {"blocked:b-old", "blocked:b-anon", "blocked:b-young"}


def test_actions_are_deduplicated_within_remind_s_across_agent_objects(autonomy_root):
    _open_blocks(autonomy_root)
    first = agent(autonomy_root, hours={})
    assert len(first.tick(NOW)) == 2
    assert first.tick(NOW + 60.0) == []
    assert agent(autonomy_root, hours={}).tick(NOW + 120.0) == []  # state lives in <root>/agent/state.json
    assert (autonomy_root / "agent" / "state.json").exists()
    again = agent(autonomy_root, hours={}).tick(NOW + 86400.0 + 1.0)
    # past remind_s the two alerts repeat; b-young has now been open over a day, so it alerts for the first time
    assert {action.key for action in again} == {"blocked:b-old", "blocked:b-anon", "blocked:b-young"}


def test_remind_s_is_configurable(autonomy_root):
    _open_blocks(autonomy_root)
    assert len(agent(autonomy_root, hours={}, remind_s=600.0).tick(NOW)) == 2
    assert agent(autonomy_root, hours={}, remind_s=600.0).tick(NOW + 300.0) == []
    assert len(agent(autonomy_root, hours={}, remind_s=600.0).tick(NOW + 601.0)) == 2


# --------------------------------------------------------------------------------------------- stale

def test_stale_open_improvements_nudge_their_owner(autonomy_root):
    board = Backlog(autonomy_root)
    stale = board.propose(title="Stale fix", causes=("clear_workcell",), owner="dev agent", proposed_by="Zach",
                          now=NOW - 8 * DAY_S)
    board.propose(title="Fresh fix", causes=("clear_workcell",), owner="dev agent", proposed_by="Zach",
                  now=NOW - 6 * DAY_S)
    old_done = board.propose(title="Old done fix", causes=("inspect",), owner="dev agent", proposed_by="Zach",
                             now=NOW - 30 * DAY_S)
    board.move(old_done.id, "doing", by="dev agent", now=NOW - 30 * DAY_S)
    board.mark_done(old_done.id, by="dev agent", evidence=("abc1234",), now=NOW - 20 * DAY_S)
    actions = agent(autonomy_root, hours={}).tick(NOW)
    assert kinds(actions) == ["stale"]
    [nudge] = actions
    assert tuple(nudge.to) == ("dev agent",)
    assert nudge.improvement == stale.id
    assert nudge.key.startswith(f"stale:{stale.id}:")
    assert len(nudge.key.rsplit(":", 1)[-1]) == len("2026-11-18")
    # de-duplicated within remind_s
    assert agent(autonomy_root, hours={}).tick(NOW + 60.0) == []


def test_stale_days_is_configurable(autonomy_root):
    board = Backlog(autonomy_root)
    board.propose(title="Fix", causes=("clear_workcell",), owner="dev agent", proposed_by="Zach", now=NOW - 2 * DAY_S)
    assert agent(autonomy_root, hours={}).tick(NOW) == []
    assert kinds(agent(autonomy_root, hours={}, stale_days=1.0).tick(NOW + 60.0)) == ["stale"]


# --------------------------------------------------------------------------------------------- outputs

def test_actions_are_logged_and_handed_to_the_notifier(proposal_root, notifier):
    _open_blocks(proposal_root)
    actions = agent(proposal_root, hours=PROPOSAL_HOURS, notifier=notifier).tick(NOW)
    assert kinds(actions) == ["propose", "propose", "propose", "blocked", "blocked"]
    logged = read_jsonl(proposal_root / "agent" / "actions.jsonl")
    assert [line["kind"] for line in logged] == kinds(actions)
    assert [line["key"] for line in logged] == [action.key for action in actions]
    assert all(line["t"] == NOW for line in logged)
    assert [action.key for action in notifier.delivered] == [action.key for action in actions]


def test_tick_without_a_notifier(proposal_root):
    actions = agent(proposal_root, hours=PROPOSAL_HOURS, notifier=None).tick(NOW)
    assert len(actions) == 3


def test_status_card_is_written(proposal_root):
    agent(proposal_root, hours=PROPOSAL_HOURS).tick(NOW)
    card = json.loads((proposal_root / "team" / "status.json").read_text(encoding="utf-8"))
    assert card["schema"] == "ai-team-status/1"
    assert card["member"] == "Robot Autonomy Agent"
    assert card["updated_t"] == NOW
    row = read_jsonl(proposal_root / "agent" / "kpi.jsonl")[-1]
    assert card["kpi"]["level"] == row["level"]
    assert card["todo"]["open"] == 3
    assert card["status"] == "idle"  # proposals only, nothing in "doing"
    assert card["blocked_on"] is None


def test_status_card_path_is_configurable(proposal_root, tmp_path):
    elsewhere = tmp_path / "team-directory" / "robot-autonomy.json"
    agent(proposal_root, hours=PROPOSAL_HOURS, status_path=str(elsewhere)).tick(NOW)
    assert json.loads(elsewhere.read_text(encoding="utf-8"))["schema"] == "ai-team-status/1"


def test_status_card_uses_the_kept_claim(proposal_root):
    claim_file = proposal_root / "agent" / "claim.json"
    claim_file.parent.mkdir(parents=True, exist_ok=True)
    claim_file.write_text(json.dumps({"status": "waiting_on_zach", "blocked_on": "a W&B sign-in"}), encoding="utf-8")
    agent(proposal_root, hours=PROPOSAL_HOURS).tick(NOW)
    card = json.loads((proposal_root / "team" / "status.json").read_text(encoding="utf-8"))
    assert card["status"] == "waiting_on_zach"
    assert card["blocked_on"] == "a W&B sign-in"


# --------------------------------------------------------------------------------------------- sources and limits

def test_tick_ingests_the_configured_sources(autonomy_root, tmp_path):
    chat = write_jsonl(
        tmp_path / "chat.jsonl",
        [{"id": "ask-1", "t": t_of("2026-11-17", 9.0), "level": "hands_on", "cause": "inspect", "robot": "arm-1"}],
    )
    source = SourceConfig(kind="labelled", path=str(chat), source="chat")
    agent(autonomy_root, hours=LEVEL_HOURS, sources=(source,)).tick(NOW)
    assert [iv.id for iv in Ledger(autonomy_root).interventions()] == ["ask-1"]
    row = read_jsonl(autonomy_root / "agent" / "kpi.jsonl")[-1]
    assert row["counted"] == 1


def test_the_agent_never_moves_work_or_rewrites_the_ledger(autonomy_root, tmp_path):
    # A robot_activity folder the agent reads (and must never write), a chat source, a done fix it will verify,
    # an open block, a stale item and proposable causes: every step of the tick runs.
    activity = tmp_path / "activity"
    write_jsonl(
        activity / "queue" / "events.jsonl",
        [{"schema": "robot-activity-event/1", "t": NOW - 3 * HOUR_S + 0.5, "kind": "block", "id": "q1",
          "robot": "arm-1", "by": "pick agent", "blocker": {"kind": "human", "why": "replug", "eta_t": None}}],
    )
    write_jsonl(activity / "samples" / "arm-1" / "2026-11-17.jsonl",
                [{"schema": "robot-activity/1", "t": t_of("2026-11-17", 9.0), "robot": "arm-1", "sample_s": 10.0,
                  "health": "fault"}])
    chat = write_jsonl(
        tmp_path / "chat.jsonl",
        [{"id": f"ask-{hour}", "t": t_of("2026-11-16", hour), "level": "hands_on", "cause": "belt_or_items",
          "avoidable": "yes", "fix": "belt guard", "robot": "arm-1"} for hour in range(1, 6)],
    )
    _done_fix(autonomy_root)
    _record_power(autonomy_root, BEFORE_9)
    Backlog(autonomy_root).propose(title="Stale fix", causes=("clear_workcell",), owner="dev agent",
                                   proposed_by="Zach", now=NOW - 10 * DAY_S)
    activity_before = snapshot_tree(activity)
    interventions_before = file_lines(autonomy_root / "interventions.jsonl")
    events_before = len(Backlog(autonomy_root).events())
    sources = (
        SourceConfig(kind="robot_activity", root=str(activity), faults_need_human_after_s=600.0),
        SourceConfig(kind="labelled", path=str(chat), source="chat"),
    )
    actions = agent(autonomy_root, hours=VERIFY_HOURS, baseline_days=7, sources=sources).tick(
        t_of("2026-11-18", 6.0)
    )
    assert {"verified", "propose", "blocked", "stale"} <= set(kinds(actions))
    # the robot_activity folder is untouched
    assert snapshot_tree(activity) == activity_before
    # the ledger only grew: nothing rewritten, nothing retracted
    interventions_after = file_lines(autonomy_root / "interventions.jsonl")
    assert interventions_after[: len(interventions_before)] == interventions_before
    assert not any("retract" in json.loads(line) for line in interventions_after)
    # the backlog only gained proposals and measured effects: no start, move, done or evidence by the agent
    new_events = Backlog(autonomy_root).events()[events_before:]
    assert new_events
    assert {event["kind"] for event in new_events} <= {"propose", "effect"}


# --------------------------------------------------------------------------------------------- load_config

def test_load_config(tmp_path):
    hours_path = tmp_path / "hours.jsonl"
    chat_path = tmp_path / "chat.jsonl"
    activity_root = tmp_path / "activity"
    report_path = tmp_path / "report.html"
    toml_text = f"""
name = "Test Autonomy Agent"
dispatcher = "boss"
count_levels = ["hands_on", "remote"]
window_days = 5
top_causes = 2
min_episodes = 4
block_alert_s = 900.0
tz = "UTC"
status_path = "{tmp_path / 'status.json'}"

[hours]
kind = "jsonl"
path = "{hours_path}"

[[source]]
kind = "labelled"
path = "{chat_path}"
source = "chat"
robot = "arm-1"

[[source]]
kind = "robot_activity"
root = "{activity_root}"
faults_need_human_after_s = 900.0

[links]
report = "{report_path}"
"""
    path = tmp_path / "autonomy.toml"
    path.write_text(toml_text, encoding="utf-8")
    loaded = load_config(path)
    assert isinstance(loaded, AgentConfig)
    assert loaded.name == "Test Autonomy Agent"
    assert loaded.dispatcher == "boss"
    assert loaded.owner == "Robot Autonomy Agent"  # default kept
    assert tuple(loaded.count_levels) == ("hands_on", "remote")
    assert loaded.window_days == 5
    assert loaded.top_causes == 2
    assert loaded.min_episodes == 4
    assert loaded.block_alert_s == 900.0
    assert loaded.tz == "UTC"
    assert loaded.status_path == str(tmp_path / "status.json")
    assert loaded.hours == {"kind": "jsonl", "path": str(hours_path)}
    assert len(loaded.sources) == 2
    labelled, activity = loaded.sources
    assert labelled == SourceConfig(kind="labelled", path=str(chat_path), source="chat", robot="arm-1")
    assert activity.kind == "robot_activity"
    assert activity.root == str(activity_root)
    assert activity.faults_need_human_after_s == 900.0
    assert loaded.links == {"report": str(report_path)}
    assert load_config(str(path)) == loaded


def test_root_layout(proposal_root):
    agent(proposal_root, hours=PROPOSAL_HOURS).tick(NOW)
    assert (proposal_root / "interventions.jsonl").exists()
    assert (proposal_root / "backlog" / "events.jsonl").exists()
    assert (proposal_root / "agent" / "kpi.jsonl").exists()
    assert (proposal_root / "agent" / "actions.jsonl").exists()
    assert (proposal_root / "team" / "status.json").exists()


def test_growth_waits_for_two_weeks_of_clock(tmp_path):
    # Added 2026-11-18: on BAM's first tick (6 days of clock) the fit read beta 0.33 and set on_track; a slope from
    # a few days of bring-up is an artefact, so the agent reports no trend until growth_min_days of hours.
    from robot_autonomy.agent import AgentConfig, AutonomyAgent
    from robot_autonomy.ledger import Ledger
    from robot_autonomy.schema import Intervention

    ledger = Ledger(tmp_path)
    days = day_range("2026-11-01", "2026-11-20")
    for index, day in enumerate(days):
        ledger.record(Intervention(id=f"iv-{index}", t=t_of(day, 10.0), robot="arm-1", level="hands_on",
                                   cause="power_or_replug", source="chat"))
    short = {("arm-1", day): 8.0 for day in days[:6]}
    long = {("arm-1", day): 8.0 for day in days}
    assert AutonomyAgent(tmp_path, AgentConfig(tz="UTC"), hours=short).growth() is None
    assert AutonomyAgent(tmp_path, AgentConfig(tz="UTC"), hours=long).growth() is not None
