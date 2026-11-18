"""Parity on a real robot: robot_autonomy recomputes BAM's own autonomy rollup from BAM's own labelled data.

BAM's in-house tracker (bam_ws src/dev/bam_interventions) produced the frozen numbers in
tests/data/bam_rollup_2026-11-17.oracle.json. examples/bam/export.py maps the same raw files onto robot_autonomy's
sources; these tests check that the generic episodes, causes and KPI agree with the in-house ones.

Marker `archive`: the raw data is local to one lab and never committed; every test skips cleanly without it.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.archive

ROOT = Path(__file__).resolve().parents[1]
ORACLE = json.loads((ROOT / "tests" / "data" / "bam_rollup_2026-11-17.oracle.json").read_text())
THROUGH = ORACLE["labelled_through"]
TZ = ORACLE["tz"]


def _export_module():
    spec = importlib.util.spec_from_file_location("bam_export", ROOT / "examples" / "bam" / "export.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def bam():
    export = _export_module()
    if not (export.BACKFILL / "rollup.json").exists():
        pytest.skip("BAM's intervention backfill is not on this machine")
    from robot_autonomy.sources.labelled import read_labelled

    asks = read_labelled(export.ask_rows(export.BACKFILL, through=THROUGH), source="chat", tz=TZ)
    reports = read_labelled(export.report_rows(export.BACKFILL, through=THROUGH), source="human", tz=TZ)
    return {"export": export, "asks": asks, "reports": reports}


@pytest.fixture(scope="module")
def bam_hours(bam):
    export = bam["export"]
    if not export.ACTIVITY.exists():
        pytest.skip("BAM's activity rows are not on this machine")
    return {(row["robot"], row["day"]): row["hours"]
            for row in export.hours_rows(export.ACTIVITY, through=THROUGH, basis="powered")}


def test_every_labelled_ask_and_report_maps_to_one_intervention(bam):
    assert len(bam["asks"]) == ORACLE["asks"]["mentions"]
    assert len(bam["reports"]) == ORACLE["reports"]["count"]
    assert {iv.reading for iv in bam["asks"]} == {"asked"}
    assert {iv.reading for iv in bam["reports"]} == {"observed"}


def test_repeats_merge_into_the_same_episodes(bam):
    from robot_autonomy.episodes import episodes

    eps = episodes(bam["asks"])
    asks = ORACLE["asks"]
    assert len(eps) == asks["episodes"]
    assert sum(e.level == "hands_on" for e in eps) == asks["hands_on_episodes"]
    assert sum(e.level == "remote" for e in eps) == asks["remote_episodes"]
    assert sum(e.commissioning for e in eps) == asks["commissioning_episodes"]
    assert sum(e.avoidable == "yes" for e in eps) == asks["avoidable_episodes"]
    assert sum(e.mentions for e in eps) == asks["mentions"]


def test_the_causes_table_matches_why_they_asked(bam):
    from robot_autonomy.episodes import episodes
    from robot_autonomy.metrics import causes

    rows = causes(episodes(bam["asks"]))
    mine = sorted(({"level": row.level, "category": row.cause, "episodes": row.episodes, "avoidable": row.avoidable}
                   for row in rows), key=lambda row: (row["level"], row["category"]))
    theirs = [{key: row[key] for key in ("level", "category", "episodes", "avoidable")}
              for row in ORACLE["by_category"]]
    assert mine == theirs


def test_the_7_day_kpi_on_the_asked_reading_matches(bam, bam_hours):
    from robot_autonomy.episodes import episodes
    from robot_autonomy.metrics import kpi

    eps = episodes(bam["asks"] + bam["reports"])
    for end, expected in ORACLE["kpi_7d"].items():
        result = kpi(eps, bam_hours, end=end, days=7, count_levels=("hands_on",), tz=TZ)
        asked = next(reading for reading in result.readings if reading.name == "asked")
        assert result.working_h == pytest.approx(expected["powered_h"], abs=0.05)
        assert asked.episodes == expected["hands_on_clocked"]
        assert asked.mtbi_h == pytest.approx(expected["mtbi_asks_h"], abs=0.01)
        # The held level is the lower reading; the asked reading alone sits at R1 in both weeks.
        assert result.level == expected["level_asks"]
