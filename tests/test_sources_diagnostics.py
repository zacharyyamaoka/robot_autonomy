"""sources/diagnostics.py: robot_msgs DiagnosticRecord streams (SPEC "sources/")."""

from __future__ import annotations

import pytest

from helpers import t_of, write_jsonl
from robot_autonomy.schema import Intervention
from robot_autonomy.sources.diagnostics import read_diagnostics

T0 = t_of("2026-11-02", 8.0)


def record(stamp_offset: float, path: str, level: str, code: str = "", message: str = "") -> dict:
    from robot_msgs.std import DiagnosticRecord, Header

    return DiagnosticRecord(
        header=Header(stamp=T0 + stamp_offset), path=path, level=level, code=code, message=message
    ).to_dict()


@pytest.fixture
def diagnostics_file(tmp_path):
    bad_level = record(1022, "arm-1.can.bus", "ok")
    bad_level["level"] = "bogus"  # DiagnosticRecord.from_dict raises: skipped
    lines = [
        record(1000, "arm-1.can.bus", "ok"),
        record(1010, "arm-1.can.bus", "error", "BusDown", "CAN bus down"),
        record(1015, "arm-2.camera", "error", "CameraLost", "camera lost"),
        record(1020, "arm-1.can.bus", "error", "BusDown", "CAN bus down"),
        "{not json at all",
        bad_level,
        {"path": "arm-1.can.bus", "level": "error"},  # no header: invalid, skipped
        record(1025, "arm-2.camera", "error", "CameraLost", "camera lost"),
        # arm-1.can.bus streak 1 ends at the first later non-error record: 1010 -> 1030 = 20 s (self-recovery)
        record(1030, "arm-1.can.bus", "ok"),
        # arm-1.can.bus streak 2: 2000 -> 2700 (a warn ends it) = 700 s (intervention); empty message -> what = code
        record(2000, "arm-1.can.bus", "error", "MotorFault.33"),
        record(2300, "arm-1.can.bus", "error", "MotorFault.33"),
        record(2700, "arm-1.can.bus", "warn", "Overheat", "warm"),
        # arm-2.lights: no record follows its streak: it ends at its last error record, 3000 -> 3700 = 700 s
        record(3000, "arm-2.lights", "error", "LightsOff", "lights off"),
        record(3700, "arm-2.lights", "error", "LightsOff", "lights off"),
        # arm-2.camera's streak (1015 -> 1025, 10 s) also has no later record: a self-recovery
    ]
    return write_jsonl(tmp_path / "diag.jsonl", lines)


def test_long_error_streaks_become_interventions(diagnostics_file):
    interventions, _recoveries = read_diagnostics(diagnostics_file, needs_human_after_s=600.0)
    assert all(isinstance(iv, Intervention) for iv in interventions)
    by_id = {iv.id: iv for iv in interventions}
    motor_id = f"diag:arm-1.can.bus:{T0 + 2000:.0f}"
    lights_id = f"diag:arm-2.lights:{T0 + 3000:.0f}"
    assert set(by_id) == {motor_id, lights_id}
    motor = by_id[motor_id]
    assert motor.t == pytest.approx(T0 + 2000)
    assert motor.level == "hands_on"
    assert motor.cause == "MotorFault.33"
    assert motor.source == "diagnostics"
    assert motor.reading == "observed"
    assert motor.what == "MotorFault.33"  # no message: the code
    assert motor.robot == "arm-1"  # first dotted segment of the path
    assert motor.downtime_s == pytest.approx(700.0)
    lights = by_id[lights_id]
    assert lights.what == "lights off"
    assert lights.cause == "LightsOff"
    assert lights.robot == "arm-2"
    assert lights.downtime_s == pytest.approx(700.0)


def test_short_error_streaks_are_self_recoveries(diagnostics_file):
    _interventions, recoveries = read_diagnostics(diagnostics_file, needs_human_after_s=600.0)
    assert sorted(recoveries, key=lambda recovery: recovery["start_t"]) == [
        {"robot": "arm-1", "path": "arm-1.can.bus", "start_t": T0 + 1010, "end_t": T0 + 1030, "seconds": 20.0,
         "code": "BusDown"},
        {"robot": "arm-2", "path": "arm-2.camera", "start_t": T0 + 1015, "end_t": T0 + 1025, "seconds": 10.0,
         "code": "CameraLost"},
    ]


def test_robot_of(diagnostics_file):
    interventions, recoveries = read_diagnostics(
        diagnostics_file, needs_human_after_s=600.0, robot_of=lambda path: "cell-9"
    )
    assert {iv.robot for iv in interventions} == {"cell-9"}
    assert {recovery["robot"] for recovery in recoveries} == {"cell-9"}


def test_threshold(diagnostics_file):
    interventions, recoveries = read_diagnostics(diagnostics_file, needs_human_after_s=15.0)
    # 20 s, 700 s and 700 s streaks are long enough; the 10 s camera streak is not
    assert len(interventions) == 3
    assert [recovery["code"] for recovery in recoveries] == ["CameraLost"]
    bus = [iv for iv in interventions if iv.id == f"diag:arm-1.can.bus:{T0 + 1010:.0f}"]
    assert len(bus) == 1
    assert bus[0].what == "CAN bus down"
    assert bus[0].downtime_s == pytest.approx(20.0)
