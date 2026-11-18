"""robot_msgs DiagnosticRecord streams (JSONL of DiagnosticRecord.to_dict()): errors a person had to clear.

The only module in the package that imports robot_msgs (inside the function, so importing this module stays light).
"""

from __future__ import annotations

import json
from pathlib import Path

from ..schema import Intervention


def _first_segment(path: str) -> str:
    return path.split(".", 1)[0]


def read_diagnostics(path, *, needs_human_after_s=600.0, robot_of=None) -> tuple[list[Intervention], list[dict]]:
    """Per component path, an error streak is a maximal run of consecutive records with level "error"; it starts at
    its first record's stamp and ends at the stamp of the first later record that is not an error (or its last error
    record when none follows). Long enough -> a hands_on Intervention (cause = the code); shorter -> a self-recovery
    {"robot", "path", "start_t", "end_t", "seconds", "code"}."""
    from robot_msgs.std import DiagnosticRecord

    robot_of = robot_of or _first_segment
    by_path: dict[str, list] = {}
    file_path = Path(path)
    if file_path.exists():
        with open(file_path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = DiagnosticRecord.from_dict(json.loads(line))
                except Exception:  # noqa: BLE001 - any line robot_msgs refuses is skipped, whatever it raises
                    continue
                by_path.setdefault(record.path, []).append(record)

    interventions: list[Intervention] = []
    recoveries: list[dict] = []
    stream_end = max((record.header.stamp for records in by_path.values() for record in records), default=None)
    for component, records in by_path.items():
        # SPEC: records are walked in stamp order (stable, so equal stamps keep file order).
        records.sort(key=lambda record: record.header.stamp)
        streak: list = []
        for record in records:
            if record.level == "error":
                streak.append(record)
                continue
            if streak:
                _emit(component, streak, record.header.stamp, needs_human_after_s=needs_human_after_s,
                      robot_of=robot_of, interventions=interventions, recoveries=recoveries, still_open=False)
                streak = []
        if streak:
            # No later record on this path: the streak ends at its last error record. It is still open only when
            # that record is the stream's last one (SPEC clarification 6: then a short one is not over yet).
            _emit(component, streak, streak[-1].header.stamp, needs_human_after_s=needs_human_after_s,
                  robot_of=robot_of, interventions=interventions, recoveries=recoveries,
                  still_open=streak[-1].header.stamp >= stream_end)
    interventions.sort(key=lambda intervention: (intervention.t, intervention.id))
    recoveries.sort(key=lambda recovery: (recovery["start_t"], recovery["path"]))
    return interventions, recoveries


def _emit(component, streak, end, *, needs_human_after_s, robot_of, interventions, recoveries, still_open) -> None:
    first = streak[0]
    start = first.header.stamp
    seconds = end - start
    if seconds >= needs_human_after_s:
        interventions.append(Intervention(
            id=f"diag:{component}:{start:.0f}", t=start, robot=robot_of(component), level="hands_on", cause=first.code,
            source="diagnostics", what=first.message or first.code, downtime_s=seconds))
    elif not still_open:
        # SPEC: a path that went quiet while the stream carried on (no later record on it) is over: a self-recovery.
        recoveries.append({"robot": robot_of(component), "path": component, "start_t": start, "end_t": end,
                           "seconds": seconds, "code": first.code})
