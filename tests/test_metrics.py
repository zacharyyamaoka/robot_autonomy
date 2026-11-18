"""metrics.py: ladder, MTBI, kpi, causes and backlog order (SPEC "metrics.py")."""

from __future__ import annotations

import pytest

from helpers import day_range, hours_table, make_cause, make_episode, t_of
from robot_autonomy import metrics
from robot_autonomy.metrics import ROBOT_LADDER, Gate, Kpi, Reading
from robot_autonomy.schema import Block

# --------------------------------------------------------------------------------------------- ladder and mtbi


def test_robot_ladder_is_the_spec_ladder():
    assert [(gate.level, gate.name, gate.hours) for gate in ROBOT_LADDER] == [
        ("R0", "Bring-up", 0.0),
        ("R1", "Assisted", 1.0),
        ("R2", "Shift", 8.0),
        ("R3", "Overnight", 24.0),
        ("R4", "Lights-out", 168.0),
    ]
    assert all(isinstance(gate, Gate) and gate.meaning for gate in ROBOT_LADDER)


@pytest.mark.parametrize(
    ("mtbi_hours", "expected_level"),
    [
        (None, "R0"),
        (0.0, "R0"),
        (0.99, "R0"),
        (1.0, "R1"),
        (7.99, "R1"),
        (8.0, "R2"),
        (23.9, "R2"),
        (24.0, "R3"),
        (167.0, "R3"),
        (168.0, "R4"),
        (10_000.0, "R4"),
    ],
)
def test_level_is_the_highest_gate_cleared(mtbi_hours, expected_level):
    assert metrics.level(ROBOT_LADDER, mtbi_hours).level == expected_level


def test_held_is_the_lower_of_the_readings():
    assert metrics.held(ROBOT_LADDER, 10.0, 2.0).level == "R1"
    assert metrics.held(ROBOT_LADDER, 2.0, 10.0).level == "R1"
    assert metrics.held(ROBOT_LADDER, 30.0, 200.0).level == "R3"
    assert metrics.held(ROBOT_LADDER, 10.0, None).level == "R0"
    assert metrics.held(ROBOT_LADDER, 9.0).level == "R2"


def test_next_gate():
    by_level = {gate.level: gate for gate in ROBOT_LADDER}
    assert metrics.next_gate(ROBOT_LADDER, by_level["R0"]).level == "R1"
    assert metrics.next_gate(ROBOT_LADDER, by_level["R1"]).level == "R2"
    assert metrics.next_gate(ROBOT_LADDER, by_level["R3"]).level == "R4"
    assert metrics.next_gate(ROBOT_LADDER, by_level["R4"]) is None


def test_mtbi():
    assert metrics.mtbi(None, 3) is None
    assert metrics.mtbi(0.0, 0) is None  # falsy hours
    assert metrics.mtbi(0.0, 2) is None
    assert metrics.mtbi(10.0, 4) == pytest.approx(2.5)
    assert metrics.mtbi(10.0, 0) == pytest.approx(10.0)  # at least that long


# --------------------------------------------------------------------------------------------- kpi

# Window: 2026-11-01 .. 2026-11-07 (end="2026-11-07", days=7), tz UTC.
KPI_HOURS = {
    ("arm-1", "2026-10-31"): 10.0,  # outside the window
    ("arm-1", "2026-11-01"): 4.0,
    ("arm-1", "2026-11-02"): 4.0,
    # 2026-11-03: no hours at all
    ("arm-1", "2026-11-04"): 6.0,
    ("arm-1", "2026-11-05"): 2.0,
    ("arm-2", "2026-11-05"): 4.0,
    ("arm-1", "2026-11-08"): 5.0,  # outside the window
}
# In-window hours: 4 + 4 + 6 + 2 + 4 = 20.


def _kpi_episodes():
    return [
        # E1 counted, asked, avoidable, human_s unknown (hands_on default 300 s)
        make_episode("E1", t_of("2026-11-01", 10.0), cause="power_or_replug", avoidable="yes"),
        # E2 counted, observed, human_s 600
        make_episode(
            "E2", t_of("2026-11-02", 9.0), cause="belt_or_items", reading="observed", source="operator",
            avoidable="no", human_s=600.0,
        ),
        # E3 on a day without hours: unclocked, never counted (and not in hands_on/avoidable)
        make_episode("E3", t_of("2026-11-03", 12.0), cause="power_or_replug", avoidable="yes"),
        # E4 remote: clocked, not counted with count_levels=("hands_on",); human_s unknown (remote default 30 s)
        make_episode("E4", t_of("2026-11-04", 8.0), level="remote", cause="run_robot_command", avoidable="yes"),
        # E5 commissioning: clocked, never counted; human_s 1200
        make_episode(
            "E5", t_of("2026-11-04", 15.0), cause="mechanical", reading="observed", source="operator",
            commissioning=True, human_s=1200.0,
        ),
        # E6 arm-2, counted, asked, human_s 60
        make_episode("E6", t_of("2026-11-05", 11.0), robot="arm-2", cause="lights_or_cameras", human_s=60.0),
        # E7 / E8 outside the window
        make_episode("E7", t_of("2026-10-31", 12.0), cause="power_or_replug"),
        make_episode("E8", t_of("2026-11-08", 12.0), cause="power_or_replug"),
        # E9 counted, asked, avoidable no, human_s unknown (300 s)
        make_episode("E9", t_of("2026-11-05", 13.0), cause="other_hands_on", avoidable="no"),
    ]


def test_kpi_counts_and_readings():
    result = metrics.kpi(_kpi_episodes(), KPI_HOURS, end="2026-11-07", days=7, tz="UTC")
    assert isinstance(result, Kpi)
    assert result.start == "2026-11-01"
    assert result.end == "2026-11-07"
    assert result.working_h == pytest.approx(20.0)
    # counted: E1, E2, E6, E9 (in window, clocked, hands_on, not commissioning)
    assert result.counted == 4
    # every in-window clocked episode: hands_on E1 E2 E5 E6 E9; remote E4; commissioning E5; avoidable E1 E4
    assert result.hands_on == 5
    assert result.remote == 1
    assert result.commissioning == 1
    assert result.avoidable == 2
    assert result.unclocked == 1  # E3
    # asked: E1, E6, E9 -> 20 / 3; observed: E2 -> 20 / 1
    assert [reading.name for reading in result.readings] == ["asked", "observed"]
    asked, observed = result.readings
    assert isinstance(asked, Reading)
    assert asked.episodes == 3
    assert asked.mtbi_h == pytest.approx(20.0 / 3.0)
    assert observed.episodes == 1
    assert observed.mtbi_h == pytest.approx(20.0)
    assert result.mtbi_h == pytest.approx(20.0 / 3.0)  # the lower of the readings
    # held: asked 6.67 h -> R1, observed 20 h -> R2; the lower wins
    assert result.level == "R1"
    assert result.level_name == "Assisted"
    assert result.next_level == "R2"
    assert result.next_gate_h == pytest.approx(8.0)
    # human seconds: E1 300 (default) + E2 600 + E4 30 (remote default) + E5 1200 + E6 60 + E9 300 = 2490 over 20 h
    assert result.human_s_per_h == pytest.approx(2490.0 / 20.0)
    assert result.blocked_h == pytest.approx(0.0)


def test_kpi_robots_filter_applies_to_hours_and_episodes():
    result = metrics.kpi(_kpi_episodes(), KPI_HOURS, end="2026-11-07", days=7, robots=["arm-1"], tz="UTC")
    # hours 4 + 4 + 6 + 2 = 16; E6 (arm-2) gone
    assert result.working_h == pytest.approx(16.0)
    assert result.counted == 3  # E1, E2, E9
    asked, observed = result.readings
    assert asked.episodes == 2
    assert asked.mtbi_h == pytest.approx(8.0)
    assert observed.mtbi_h == pytest.approx(16.0)
    assert result.level == "R2"
    assert result.next_level == "R3"
    assert result.next_gate_h == pytest.approx(24.0)
    assert result.hands_on == 4  # E1 E2 E5 E9
    # 300 + 600 + 30 + 1200 + 300 = 2430 over 16 h
    assert result.human_s_per_h == pytest.approx(2430.0 / 16.0)


def test_kpi_count_levels_can_include_remote():
    result = metrics.kpi(
        _kpi_episodes(), KPI_HOURS, end="2026-11-07", days=7, count_levels=("hands_on", "remote"), tz="UTC"
    )
    assert result.counted == 5  # + E4
    asked, _observed = result.readings
    assert asked.episodes == 4
    assert asked.mtbi_h == pytest.approx(5.0)


def test_kpi_human_seconds_default_is_overridable():
    result = metrics.kpi(
        _kpi_episodes(),
        KPI_HOURS,
        end="2026-11-07",
        days=7,
        human_s_default={"hands_on": 100.0, "remote": 10.0},
        tz="UTC",
    )
    # 100 + 600 + 10 + 1200 + 60 + 100 = 2070 over 20 h
    assert result.human_s_per_h == pytest.approx(2070.0 / 20.0)


def test_kpi_with_no_hours_in_the_window():
    only_outside = {("arm-1", "2026-10-31"): 10.0}
    result = metrics.kpi(_kpi_episodes(), only_outside, end="2026-11-07", days=7, tz="UTC")
    assert result.working_h is None
    assert result.counted == 0
    assert all(reading.mtbi_h is None for reading in result.readings)
    assert result.mtbi_h is None
    assert result.level == "R0"
    assert result.next_level == "R1"
    assert result.human_s_per_h is None
    # every in-window episode is unclocked: E1 E2 E3 E4 E5 E6 E9
    assert result.unclocked == 7


def test_kpi_with_no_episodes_reads_the_whole_span():
    result = metrics.kpi([], KPI_HOURS, end="2026-11-07", days=7, tz="UTC")
    assert result.counted == 0
    assert result.mtbi_h == pytest.approx(20.0)  # at least that long
    assert result.level == "R2"


def test_kpi_window_length():
    # days=3 -> 2026-11-05 .. 2026-11-07: hours 2 + 4 = 6; counted E6 (asked) and E9 (asked) -> 6 / 2 = 3
    result = metrics.kpi(_kpi_episodes(), KPI_HOURS, end="2026-11-07", days=3, tz="UTC")
    assert result.start == "2026-11-05"
    assert result.working_h == pytest.approx(6.0)
    assert result.counted == 2
    assert result.mtbi_h == pytest.approx(3.0)


def test_kpi_window_day_uses_tz():
    # 2026-11-08 03:00 UTC is 2026-11-07 19:00 PST: in the window only when the day is taken in Vancouver.
    episode = make_episode("late", t_of("2026-11-08", 3.0))
    hours = {("arm-1", "2026-11-07"): 10.0}
    assert metrics.kpi([episode], hours, end="2026-11-07", days=7, tz="UTC").counted == 0
    assert metrics.kpi([episode], hours, end="2026-11-07", days=7, tz="America/Vancouver").counted == 1


def _blocks():
    return [
        # B1 straddles the window start: 2026-10-31 22:00 -> 2026-11-01 02:00, 2 h inside
        Block(id="B1", robot="arm-1", cause="approve_robot_run", why="go?",
              since_t=t_of("2026-10-31", 22.0), until_t=t_of("2026-11-01", 2.0)),
        # B4 fully inside: 1.5 h
        Block(id="B4", robot="arm-1", cause="answer_robot_question", why="which bin?",
              since_t=t_of("2026-11-03", 0.0), until_t=t_of("2026-11-03", 1.5)),
        # B2 open since 2026-11-07 20:00 (arm-2): up to now (default: the end of the window, 2026-11-08 00:00) = 4 h
        Block(id="B2", robot="arm-2", cause="approve_robot_run", why="go?", since_t=t_of("2026-11-07", 20.0)),
        # B3 opens after the window: 0 h
        Block(id="B3", robot="arm-1", cause="approve_robot_run", why="go?", since_t=t_of("2026-11-08", 1.0)),
    ]


def test_kpi_blocked_hours_are_clipped_to_the_window():
    result = metrics.kpi([], KPI_HOURS, end="2026-11-07", days=7, blocks=_blocks(), tz="UTC")
    assert result.blocked_h == pytest.approx(2.0 + 1.5 + 4.0)


def test_kpi_blocked_hours_run_up_to_now_for_open_blocks():
    now = t_of("2026-11-07", 22.0)
    result = metrics.kpi([], KPI_HOURS, end="2026-11-07", days=7, blocks=_blocks(), now=now, tz="UTC")
    # B2 open 20:00 -> 22:00 = 2 h; B3 starts after now: 0
    assert result.blocked_h == pytest.approx(2.0 + 1.5 + 2.0)


def test_kpi_blocked_hours_follow_the_robots_filter():
    result = metrics.kpi([], KPI_HOURS, end="2026-11-07", days=7, blocks=_blocks(), robots=["arm-1"], tz="UTC")
    assert result.blocked_h == pytest.approx(2.0 + 1.5)


# --------------------------------------------------------------------------------------------- causes

NOW = t_of("2026-11-18", 0.0)  # recent_days=7 -> recent means first_t >= 2026-11-11 00:00


def _cause_episodes():
    return [
        # power_or_replug (hands_on): 3 episodes, 2 avoidable; fixes: strain relief x1 (first seen), powered hub x2,
        # label cables x1 -> ("powered hub", "strain relief", "label cables"); recent: P2, P3
        make_episode("P1", t_of("2026-11-01", 1.0), avoidable="yes", fixes=("strain relief",)),
        make_episode("P2", t_of("2026-11-12", 1.0), avoidable="yes", fixes=("powered hub",)),
        make_episode("P3", t_of("2026-11-15", 1.0), avoidable="no", fixes=("powered hub", "label cables")),
        # belt_or_items (hands_on): 3 episodes, 1 avoidable; recent: 1
        make_episode("B1", t_of("2026-11-02", 1.0), cause="belt_or_items", avoidable="yes"),
        make_episode("B2", t_of("2026-11-03", 1.0), cause="belt_or_items", avoidable="no"),
        make_episode("B3", t_of("2026-11-16", 1.0), cause="belt_or_items", avoidable="no"),
        # mechanical (hands_on): 2 episodes, both commissioning, 2 avoidable
        make_episode("M1", t_of("2026-11-04", 1.0), cause="mechanical", avoidable="yes", commissioning=True),
        make_episode("M2", t_of("2026-11-05", 1.0), cause="mechanical", avoidable="yes", commissioning=True),
        # answer_robot_question (remote): 2 episodes, 1 avoidable; recent: 1
        make_episode("R1", t_of("2026-11-06", 1.0), level="remote", cause="answer_robot_question", avoidable="yes"),
        make_episode("R2", t_of("2026-11-17", 1.0), level="remote", cause="answer_robot_question"),
        # inspect (hands_on): 2 episodes, 0 avoidable, only one commissioning -> commissioning False
        make_episode("I1", t_of("2026-11-07", 1.0), cause="inspect", avoidable="no", commissioning=True),
        make_episode("I2", t_of("2026-11-08", 1.0), cause="inspect", avoidable="no"),
        # clear_workcell (hands_on): 2 episodes, 1 avoidable (ties answer_robot_question; "hands_on" < "remote")
        make_episode("C1", t_of("2026-11-09", 1.0), cause="clear_workcell", avoidable="yes"),
        make_episode("C2", t_of("2026-11-10", 1.0), cause="clear_workcell", avoidable="unclear"),
    ]


def test_causes_group_count_and_sort():
    rows = metrics.causes(_cause_episodes(), now=NOW, recent_days=7)
    # sorted by (-episodes, -avoidable, level, cause)
    assert [(row.level, row.cause) for row in rows] == [
        ("hands_on", "power_or_replug"),  # 3, 2
        ("hands_on", "belt_or_items"),  # 3, 1
        ("hands_on", "mechanical"),  # 2, 2
        ("hands_on", "clear_workcell"),  # 2, 1, hands_on
        ("remote", "answer_robot_question"),  # 2, 1, remote
        ("hands_on", "inspect"),  # 2, 0
    ]
    by_cause = {row.cause: row for row in rows}
    power = by_cause["power_or_replug"]
    assert power.episodes == 3
    assert power.avoidable == 2
    assert power.share == pytest.approx(3 / 14)
    assert power.fixes == ("powered hub", "strain relief", "label cables")
    assert power.recent == 2
    assert power.last_t == pytest.approx(t_of("2026-11-15", 1.0))
    assert power.commissioning is False
    assert by_cause["belt_or_items"].avoidable == 1
    assert by_cause["belt_or_items"].recent == 1
    assert by_cause["belt_or_items"].fixes == ()
    assert by_cause["mechanical"].commissioning is True
    assert by_cause["mechanical"].recent == 0
    assert by_cause["inspect"].commissioning is False  # not every episode is commissioning
    assert by_cause["answer_robot_question"].recent == 1
    assert sum(row.share for row in rows) == pytest.approx(1.0)


def test_causes_since_until():
    rows = metrics.causes(_cause_episodes(), since=t_of("2026-11-11"), until=t_of("2026-11-16", 1.0), now=NOW)
    # since <= first_t < until: P2, P3 only (B3 is exactly at `until`, excluded)
    assert [(row.cause, row.episodes) for row in rows] == [("power_or_replug", 2)]
    assert rows[0].share == pytest.approx(1.0)
    assert rows[0].fixes == ("powered hub", "label cables")


def test_causes_keeps_at_most_eight_fixes():
    many = [
        make_episode(f"F{index}", t_of("2026-11-01", float(index)), fixes=(f"fix {index}",)) for index in range(10)
    ]
    [row] = metrics.causes(many, now=NOW)
    assert row.fixes == tuple(f"fix {index}" for index in range(8))  # all tie at 1: first seen order


def test_causes_recent_days():
    rows = metrics.causes(_cause_episodes(), now=NOW, recent_days=3)
    # since 2026-11-15 00:00: P3, B3, R2
    by_cause = {row.cause: row for row in rows}
    assert by_cause["power_or_replug"].recent == 1
    assert by_cause["belt_or_items"].recent == 1
    assert by_cause["answer_robot_question"].recent == 1
    assert by_cause["clear_workcell"].recent == 0


def test_backlog_order():
    rows = [
        make_cause("a_cause", avoidable=2, episodes=5, recent=1),
        make_cause("bbb", avoidable=2, episodes=5, recent=3),
        make_cause("c_cause", avoidable=3, episodes=3, recent=0),
        make_cause("d_never", avoidable=0, episodes=10, recent=9),
        make_cause("e_cause", avoidable=2, episodes=6, recent=0),
        make_cause("aaa", avoidable=2, episodes=5, recent=3),
    ]
    ordered = metrics.backlog_order(rows)
    # avoidable > 0 only; (-avoidable, -episodes, -recent, cause)
    assert [row.cause for row in ordered] == ["c_cause", "e_cause", "aaa", "bbb", "a_cause"]


def test_backlog_order_of_nothing():
    assert metrics.backlog_order([]) == []
    assert metrics.backlog_order([make_cause("x", avoidable=0, episodes=4)]) == []


# --------------------------------------------------------------------------------------------- daily


def test_daily_rows():
    hours = {("arm-1", "2026-11-01"): 2.0, ("arm-1", "2026-11-03"): 4.0}
    episodes = [
        make_episode("on-day-1", t_of("2026-11-01", 10.0)),
        make_episode("unclocked", t_of("2026-11-02", 10.0)),
    ]
    rows = metrics.daily(episodes, hours, window_days=2, tz="UTC")
    assert [row["day"] for row in rows] == ["2026-11-01", "2026-11-02", "2026-11-03"]
    day_1, day_2, day_3 = rows
    # 2026-11-01: 1 counted; window (10-31, 11-01): 2 h / 1 episode = 2 h -> R1
    assert day_1["working_h"] == pytest.approx(2.0)
    assert day_1["episodes"] == 1
    assert day_1["mtbi_h"] == pytest.approx(2.0)
    assert day_1["level"] == "R1"
    # 2026-11-02: no hours, its episode is unclocked (0 counted); window (11-01, 11-02): 2 h / 1 = 2 h
    assert not day_2["working_h"]
    assert day_2["episodes"] == 0
    assert day_2["mtbi_h"] == pytest.approx(2.0)
    assert day_2["level"] == "R1"
    # 2026-11-03: window (11-02, 11-03): 4 h, 0 counted -> 4 h
    assert day_3["working_h"] == pytest.approx(4.0)
    assert day_3["episodes"] == 0
    assert day_3["mtbi_h"] == pytest.approx(4.0)
    assert day_3["level"] == "R1"


def test_daily_matches_kpi_for_each_window():
    hours = hours_table("arm-1", day_range("2026-11-01", "2026-11-10"), 3.0)
    episodes = [make_episode(f"e{index}", t_of("2026-11-01", 5.0) + index * 86400.0 * 1.5) for index in range(6)]
    rows = metrics.daily(episodes, hours, window_days=7, tz="UTC")
    for row in rows:
        window = metrics.kpi(episodes, hours, end=row["day"], days=7, tz="UTC")
        assert row["level"] == window.level
        assert row["mtbi_h"] == pytest.approx(window.mtbi_h)
