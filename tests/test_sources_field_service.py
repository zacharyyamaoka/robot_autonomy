"""sources/field_service.py: repair / service tickets (SPEC "sources/")."""

from __future__ import annotations

import csv

import pytest

from helpers import t_of, write_jsonl
from robot_autonomy.sources.field_service import read_tickets

COLUMNS = ["id", "robot", "opened", "closed", "cause", "what", "onsite", "technician_h", "site"]


def _write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in COLUMNS})
    return path


def test_csv_tickets(tmp_path):
    path = _write_csv(
        tmp_path / "tickets.csv",
        [
            {"id": "T-1", "robot": "arm-1", "opened": "2026-11-02T10:00:00Z", "closed": "2026-11-02T14:00:00Z",
             "cause": "belt_or_items", "what": "replaced the belt", "onsite": "true", "technician_h": "1.5",
             "site": "Site A"},
            {"id": "T-2", "robot": "arm-2", "opened": str(t_of("2026-11-03", 8.0)), "closed": "",
             "cause": "run_robot_command", "what": "remote restart", "onsite": "no", "technician_h": "", "site": ""},
        ],
    )
    by_id = {iv.id: iv for iv in read_tickets(path, tz="UTC")}
    assert set(by_id) == {"ticket:T-1", "ticket:T-2"}
    onsite = by_id["ticket:T-1"]
    assert onsite.t == pytest.approx(t_of("2026-11-02", 10.0))
    assert onsite.robot == "arm-1"
    assert onsite.level == "hands_on"
    assert onsite.cause == "belt_or_items"
    assert onsite.what == "replaced the belt"
    assert onsite.source == "field_service"
    assert onsite.reading == "observed"
    assert onsite.thread == "ticket:T-1"
    assert onsite.human_s == pytest.approx(1.5 * 3600)
    assert onsite.downtime_s == pytest.approx(4 * 3600)
    assert onsite.evidence == "Site A"
    remote = by_id["ticket:T-2"]
    assert remote.t == pytest.approx(t_of("2026-11-03", 8.0))  # unix seconds
    assert remote.level == "remote"
    assert remote.thread == "ticket:T-2"
    assert remote.human_s is None  # technician_h not given
    assert remote.downtime_s is None  # not closed


@pytest.mark.parametrize(
    ("onsite", "level"),
    [("true", "hands_on"), ("1", "hands_on"), ("yes", "hands_on"), ("false", "remote"), ("0", "remote"),
     ("no", "remote")],
)
def test_onsite_spellings(tmp_path, onsite, level):
    path = _write_csv(
        tmp_path / "tickets.csv",
        [{"id": "T-9", "robot": "arm-1", "opened": "2026-11-02T10:00:00Z", "cause": "inspect", "what": "look",
          "onsite": onsite}],
    )
    [ticket] = read_tickets(path, tz="UTC")
    assert ticket.level == level


def test_naive_iso_is_read_in_tz(tmp_path):
    path = _write_csv(
        tmp_path / "tickets.csv",
        [{"id": "T-3", "robot": "arm-1", "opened": "2026-11-02T10:00:00", "closed": "2026-11-02T10:30:00",
          "cause": "inspect", "what": "look", "onsite": "yes"}],
    )
    [ticket] = read_tickets(path, tz="UTC")
    assert ticket.t == pytest.approx(t_of("2026-11-02", 10.0))
    assert ticket.downtime_s == pytest.approx(1800.0)


def test_jsonl_tickets(tmp_path):
    path = write_jsonl(
        tmp_path / "tickets.jsonl",
        [{"id": "T-4", "robot": "arm-1", "opened": t_of("2026-11-04", 9.0), "closed": t_of("2026-11-04", 11.0),
          "cause": "power_or_replug", "what": "new PSU", "onsite": True, "technician_h": 2.0, "site": "Site B"}],
    )
    [ticket] = read_tickets(path, tz="UTC")
    assert ticket.id == "ticket:T-4"
    assert ticket.level == "hands_on"
    assert ticket.human_s == pytest.approx(7200.0)
    assert ticket.downtime_s == pytest.approx(7200.0)
    assert ticket.evidence == "Site B"
    assert ticket.source == "field_service"
