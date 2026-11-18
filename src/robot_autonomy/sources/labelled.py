"""Any labelled log of asks: a chat labeller's output, a hook's ledger, an operator's own log."""

from __future__ import annotations

from pathlib import Path

from .._files import read_jsonl
from ..schema import AVOIDABLE, LEVELS, Intervention, parse_time


def _rows(rows_or_path) -> list[dict]:
    if isinstance(rows_or_path, (str, Path)):
        return read_jsonl(rows_or_path)
    return [row for row in rows_or_path if isinstance(row, dict)]


def _optional_float(value):
    if value is None or value == "":
        return None
    return float(value)


def read_labelled(rows_or_path, *, source="chat", reading=None, robot=None, tz=None) -> list[Intervention]:
    """Rows (dicts, or a JSONL path) with id; t (unix) or ts (ISO 8601, "Z" allowed, naive read in tz); level;
    cause or category; robot (or the `robot` argument); optional what, avoidable, fix, thread or session, by, human_s,
    downtime_s, evidence. Rows whose level is not in LEVELS ("none") are skipped; an unknown avoidable reads "unclear".
    """
    out = []
    for row in _rows(rows_or_path):
        level = row.get("level")
        if level not in LEVELS:
            continue
        try:
            item_id = row["id"]
            if row.get("t") is not None:
                t = parse_time(row["t"], tz)
            else:
                t = parse_time(row["ts"], tz)
            cause = row.get("cause") or row.get("category")
            avoidable = row.get("avoidable")
            thread = row.get("thread") or row.get("session")
            # SPEC: a row with no robot and no `robot` argument is filed under "unknown" rather than dropped.
            intervention = Intervention(
                id=str(item_id), t=t, robot=str(row.get("robot") or robot or "unknown"), level=level, cause=cause,
                source=source, reading=reading, what=str(row.get("what") or ""),
                avoidable=avoidable if avoidable in AVOIDABLE else "unclear", fix=str(row.get("fix") or ""),
                thread=str(thread) if thread else None, by=row.get("by"),
                human_s=_optional_float(row.get("human_s")), downtime_s=_optional_float(row.get("downtime_s")),
                evidence=row.get("evidence"))
        except (KeyError, TypeError, ValueError):
            # SPEC: a row that cannot make an Intervention (no id, no time, no cause) is skipped, like a bad line.
            continue
        out.append(intervention)
    return out
