"""Nightly export and the sample readers must stream: memory is bounded by one row, not by the archive.

WHY (2026-11-25): examples/bam/export.py kept every full activity row of every day in a list before sorting; BAM's rows
carry ~1 KB of fields each, so 13 days reached 1.4 GB and the 1 GB systemd cap killed the nightly run. The reference
below is the old whole-row algorithm; the streaming one must give the same hours on a day that fits.
"""

from __future__ import annotations

import collections
import datetime as dt
import importlib.util
import json
import tracemalloc
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAD = "x" * 8000


def _export():
    spec = importlib.util.spec_from_file_location("bam_export_mem", ROOT / "examples" / "bam" / "export.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _reference_hours(activity: Path, through, basis):
    """The pre-fix algorithm, verbatim in behaviour: load every row, sort, de-duplicate by 10 s slot."""
    unpowered = {"disconnected", "no host"}
    out = []
    for robot_dir in sorted(p for p in activity.glob("*") if p.is_dir() and p.name != "timeline"):
        rows = []
        for path in sorted(robot_dir.glob("*.jsonl")):
            for line in path.read_text().splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if "t" in row:
                    rows.append(row)
        seen, days = set(), collections.defaultdict(float)
        for row in sorted(rows, key=lambda r: r["t"]):
            slot = round(row["t"] / 10)
            if slot in seen:
                continue
            seen.add(slot)
            day = dt.datetime.fromtimestamp(row["t"]).date().isoformat()
            if through and day > through:
                continue
            working = (row.get("kind") == "job" if basis == "activity" else (row.get("state") or "") not in unpowered)
            if working:
                days[day] += row.get("sample_s", 10.0) / 3600
        out += [{"robot": robot_dir.name, "day": d, "hours": round(h, 4)} for d, h in sorted(days.items())]
    return out


def _write_days(root: Path, robot: str, days: int, rows_per_day: int, pad: str = "") -> None:
    folder = root / robot
    folder.mkdir(parents=True)
    base = dt.datetime(2026, 11, 1, 0, 0, 0).timestamp()
    for day in range(days):
        with open(folder / f"2026-11-{day + 1:02d}.jsonl", "w") as handle:
            for i in range(rows_per_day):
                t = base + day * 86400 + i * 10 + (3 if i % 7 == 0 else 0)   # some rows share a 10 s slot
                row = {"t": t, "kind": "job" if i % 3 else "idle", "state": "disconnected" if i % 5 == 0 else "ready",
                       "pad": pad}
                if i % 11 == 0:
                    row["sample_s"] = 5.0
                handle.write(json.dumps(row) + "\n")
                if i % 13 == 0:
                    handle.write("{not json\n")
                if i % 17 == 0:
                    handle.write(json.dumps({"no_t": 1}) + "\n")


@pytest.mark.parametrize("basis", ["activity", "powered"])
@pytest.mark.parametrize("through", [None, "2026-11-02"])
def test_streamed_hours_match_the_whole_row_reference(tmp_path, basis, through):
    _write_days(tmp_path, "r1", days=3, rows_per_day=400)
    _write_days(tmp_path, "r2", days=2, rows_per_day=150)
    export = _export()
    assert export.hours_rows(tmp_path, through=through, basis=basis) == _reference_hours(tmp_path, through, basis)


def test_hours_rows_memory_does_not_grow_with_row_size(tmp_path):
    _write_days(tmp_path, "r1", days=3, rows_per_day=1500, pad=PAD)   # ~36 MB of rows on disk
    export = _export()
    tracemalloc.start()
    try:
        rows = export.hours_rows(tmp_path, basis="activity")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert rows
    assert peak < 8 * 1024 * 1024, f"peak {peak / 1e6:.1f} MB: rows are being retained whole"


def _samples(root: Path, robot: str, days: int, rows_per_day: int, pad: str = "") -> None:
    folder = root / "samples" / robot
    folder.mkdir(parents=True)
    base = dt.datetime(2026, 11, 1).timestamp()
    for day in range(days):
        with open(folder / f"2026-11-{day + 1:02d}.jsonl", "w") as handle:
            for i in range(rows_per_day):
                health = "fault" if 100 <= i % 1000 < 130 else "ok"
                handle.write(json.dumps({"t": base + day * 86400 + i * 10, "health": health, "item": "a",
                                         "reason": "stuck", "pad": pad}) + "\n")


def test_robot_activity_readers_memory_is_bounded(tmp_path):
    from robot_autonomy.hours import from_robot_activity
    from robot_autonomy.sources.robot_activity import read_faults

    _samples(tmp_path, "r1", days=3, rows_per_day=1500, pad=PAD)
    for reader in (lambda: from_robot_activity(tmp_path), lambda: read_faults(tmp_path)):
        tracemalloc.start()
        try:
            reader()
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert peak < 8 * 1024 * 1024, f"peak {peak / 1e6:.1f} MB"


def test_read_faults_still_finds_the_streaks(tmp_path):
    from robot_autonomy.sources.robot_activity import read_faults

    _samples(tmp_path, "r1", days=1, rows_per_day=1000)
    interventions, recoveries = read_faults(tmp_path, needs_human_after_s=200.0)
    assert len(interventions) == 1 and interventions[0].what == "stuck" and interventions[0].cause == "fault"
    assert not recoveries
