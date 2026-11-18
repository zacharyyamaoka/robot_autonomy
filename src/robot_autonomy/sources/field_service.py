"""Repair / service tickets from a deployed site (CSV with a header, or JSONL): the field's own record of a visit."""

from __future__ import annotations

import csv
from pathlib import Path

from .._files import read_jsonl
from ..schema import Intervention, parse_time

TRUE = ("true", "1", "yes", "y", "t")
FALSE = ("false", "0", "no", "n", "f", "")


def _onsite(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    text = str(value).strip().lower()
    if text in TRUE:
        return True
    if text in FALSE:
        return False
    raise ValueError(f"onsite must be true/false/1/0/yes/no, got {value!r}")


def _blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    if path.suffix.lower() in (".jsonl", ".json", ".ndjson"):
        return read_jsonl(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    if text.lstrip().startswith("{"):
        return read_jsonl(path)
    return [dict(row) for row in csv.DictReader(text.splitlines())]


def read_tickets(path, *, tz=None) -> list[Intervention]:
    """Columns: id, robot, opened (ISO or unix), closed (optional), cause, what, onsite (true/false/1/0/yes/no),
    technician_h (optional), site (optional). Onsite -> hands_on, else remote; the ticket is its own thread."""
    out = []
    for row in _rows(Path(path)):
        try:
            ticket = str(row["id"]).strip()
            opened = parse_time(row["opened"], tz)
            closed = None if _blank(row.get("closed")) else parse_time(row["closed"], tz)
            technician_h = None if _blank(row.get("technician_h")) else float(row["technician_h"])
            site = None if _blank(row.get("site")) else str(row["site"])
            out.append(Intervention(
                id=f"ticket:{ticket}", t=opened, robot=str(row["robot"]),
                level="hands_on" if _onsite(row.get("onsite")) else "remote", cause=str(row.get("cause") or ""),
                source="field_service", what=str(row.get("what") or ""), thread=f"ticket:{ticket}",
                human_s=None if technician_h is None else technician_h * 3600.0,
                downtime_s=None if closed is None else closed - opened, evidence=site))
        except (KeyError, TypeError, ValueError):
            # SPEC: a ticket row that cannot make an Intervention (missing id/robot/opened/cause) is skipped.
            continue
    return out
