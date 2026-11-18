"""hours.py: the denominator (SPEC "hours.py — the denominator")."""

from __future__ import annotations

import json

import pytest

from helpers import t_of, write_jsonl
from robot_autonomy import hours


def _sample(t, robot="arm-1", **fields):
    row = {"schema": "robot-activity/1", "t": t, "robot": robot}
    row.update(fields)
    return row


@pytest.fixture
def activity_root(tmp_path):
    """A robot_activity store with hand-countable rows (all times UTC)."""
    root = tmp_path / "activity"
    arm_1_file = root / "samples" / "arm-1" / "2026-11-02.jsonl"
    write_jsonl(
        arm_1_file,
        [
            # activity: claimed + item + health ok -> 360 s
            _sample(t_of("2026-11-02", 10.0), sample_s=360, claimed_by="pick agent", item="q1", health="ok"),
            # health key missing reads as "ok" -> activity, 360 s
            _sample(t_of("2026-11-02", 10.1), sample_s=360, claimed_by="pick agent", item="q1"),
            # fault: tracked only, 360 s
            _sample(t_of("2026-11-02", 10.2), sample_s=360, claimed_by="pick agent", item="q1", health="fault"),
            # item key missing reads as null: tracked only, 360 s
            _sample(t_of("2026-11-02", 10.3), sample_s=360, claimed_by="pick agent"),
            # claimed_by explicitly null: tracked only, 360 s
            _sample(t_of("2026-11-02", 10.4), sample_s=360, claimed_by=None, item="q1"),
            # sample_s missing: weighs 10 s; activity
            _sample(t_of("2026-11-02", 10.5), claimed_by="pick agent", item="q1", health="ok"),
            "{not json",
            # filed under 2026-11-02 but t is on 2026-11-03 (UTC): the day comes from t. activity, 720 s
            _sample(t_of("2026-11-03", 0.5), sample_s=720, claimed_by="pick agent", item="q2"),
        ],
    )
    write_jsonl(
        root / "samples" / "arm-2" / "2026-11-02.jsonl",
        [_sample(t_of("2026-11-02", 12.0), robot="arm-2", sample_s=3600, claimed_by="sort agent", item="q9")],
    )
    return root


def test_activity_basis(activity_root):
    table = hours.from_robot_activity(activity_root, basis="activity", tz="UTC")
    # arm-1 2026-11-02: 360 + 360 + 10 s = 730 s; 2026-11-03: 720 s; arm-2 2026-11-02: 3600 s.
    assert table[("arm-1", "2026-11-02")] == pytest.approx(730 / 3600)
    assert table[("arm-1", "2026-11-03")] == pytest.approx(720 / 3600)
    assert table[("arm-2", "2026-11-02")] == pytest.approx(1.0)
    assert {key for key, value in table.items() if value > 0} == {
        ("arm-1", "2026-11-02"),
        ("arm-1", "2026-11-03"),
        ("arm-2", "2026-11-02"),
    }


def test_activity_is_the_default_basis(activity_root):
    assert hours.from_robot_activity(activity_root, tz="UTC") == hours.from_robot_activity(
        activity_root, basis="activity", tz="UTC"
    )


def test_tracked_basis_counts_every_row(activity_root):
    table = hours.from_robot_activity(activity_root, basis="tracked", tz="UTC")
    # arm-1 2026-11-02: 5 x 360 + 10 = 1810 s.
    assert table[("arm-1", "2026-11-02")] == pytest.approx(1810 / 3600)
    assert table[("arm-1", "2026-11-03")] == pytest.approx(720 / 3600)
    assert table[("arm-2", "2026-11-02")] == pytest.approx(1.0)


def test_the_day_comes_from_t_in_tz(activity_root):
    # 2026-11-03 00:30 UTC is 2026-11-02 16:30 PST in Vancouver (UTC-8 after 2026-11-01), so every arm-1 row is on 11-02.
    table = hours.from_robot_activity(activity_root, basis="activity", tz="America/Vancouver")
    assert table[("arm-1", "2026-11-02")] == pytest.approx((730 + 720) / 3600)
    assert table.get(("arm-1", "2026-11-03"), 0.0) == 0.0


def test_missing_root_gives_an_empty_table(tmp_path):
    assert hours.from_robot_activity(tmp_path / "nowhere", tz="UTC") == {}


def test_from_jsonl_adds_rows_for_the_same_key(tmp_path):
    path = write_jsonl(
        tmp_path / "hours.jsonl",
        [
            {"robot": "arm-1", "day": "2026-11-02", "hours": 2.5},
            {"robot": "arm-1", "day": "2026-11-02", "hours": 1.5},
            {"robot": "arm-2", "day": "2026-11-02", "hours": 4.0},
            {"robot": "arm-1", "day": "2026-11-03", "hours": 6.0},
        ],
    )
    assert hours.from_jsonl(path) == {
        ("arm-1", "2026-11-02"): pytest.approx(4.0),
        ("arm-2", "2026-11-02"): pytest.approx(4.0),
        ("arm-1", "2026-11-03"): pytest.approx(6.0),
    }
    assert hours.from_jsonl(str(path)) == hours.from_jsonl(path)


TABLE = {
    ("arm-1", "2026-11-01"): 4.0,
    ("arm-1", "2026-11-02"): 0.0,
    ("arm-2", "2026-11-02"): 3.0,
    ("arm-2", "2026-11-03"): 0.0,
    ("arm-1", "2026-11-04"): 2.0,
}


def test_total():
    assert hours.total(TABLE) == pytest.approx(9.0)
    assert hours.total(TABLE, robots=["arm-1"]) == pytest.approx(6.0)
    assert hours.total(TABLE, robots=("arm-2",)) == pytest.approx(3.0)
    assert hours.total(TABLE, days=["2026-11-01", "2026-11-02"]) == pytest.approx(7.0)
    assert hours.total(TABLE, robots=["arm-1"], days={"2026-11-02", "2026-11-04"}) == pytest.approx(2.0)
    assert hours.total({}) == 0.0


def test_days_with_hours_sums_over_robots_and_drops_zero_days():
    assert hours.days_with_hours(TABLE) == {"2026-11-01", "2026-11-02", "2026-11-04"}
    assert hours.days_with_hours(TABLE, robots=["arm-1"]) == {"2026-11-01", "2026-11-04"}
    assert hours.days_with_hours(TABLE, robots=["arm-2"]) == {"2026-11-02"}
    assert hours.days_with_hours({}) == set()


def test_tables_are_plain_dicts_keyed_by_robot_and_day(activity_root):
    table = hours.from_robot_activity(activity_root, tz="UTC")
    assert isinstance(table, dict)
    for key, value in table.items():
        robot, day = key
        assert isinstance(robot, str) and isinstance(day, str) and len(day) == 10
        assert isinstance(value, float)
    json.dumps({f"{robot}|{day}": value for (robot, day), value in table.items()})
