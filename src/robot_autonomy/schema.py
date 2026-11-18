"""The records: one Intervention per time a person had to act, one Block per interval a robot waited on a person.

Times are unix seconds (float). Days are "YYYY-MM-DD" strings in an IANA timezone `tz` (None = system local).
"""

from __future__ import annotations

import datetime as dt
import functools
from dataclasses import dataclass, fields
from zoneinfo import ZoneInfo

SCHEMA = "robot-autonomy/1"
LEVELS = ("hands_on", "remote")
READINGS = ("asked", "observed")
AVOIDABLE = ("yes", "no", "unclear")

DEFAULT_CAUSES = {
    "hands_on": ("rehome_or_reposition_arm", "power_or_replug", "belt_or_items", "clear_workcell",
                 "lights_or_cameras", "mechanical", "inspect", "other_hands_on"),
    "remote": ("run_robot_command", "approve_robot_run", "answer_robot_question", "other_remote"),
}
# WHY apart: building new hardware is not the robot failing to run itself.
COMMISSIONING_CAUSES = ("mechanical",)
# WHY two readings: what the measured agents write ("asked") can be gamed by them (stop asking and stall, relabel an
# ask); what they cannot write ("observed": a person's own log, the robot's own faults, field tickets) cannot.
SOURCE_READING = {"chat": "asked", "agent": "asked", "queue": "asked",
                  "operator": "observed", "human": "observed", "field_service": "observed",
                  "robot": "observed", "diagnostics": "observed"}   # any other source -> "asked"


# ---------------------------------------------------------------------------------------------------------------- days

@functools.lru_cache(maxsize=64)
def _zone(tz: str) -> ZoneInfo:
    return ZoneInfo(tz)


def day_of(t: float, tz: str | None = None) -> str:
    """The "YYYY-MM-DD" of unix time `t` in `tz` (None = system local)."""
    if tz is None:
        # WHY uncached: system local time can change in-process (TZ + time.tzset()).
        return dt.datetime.fromtimestamp(float(t)).date().isoformat()
    return _day_of_zone(float(t), tz)


@functools.lru_cache(maxsize=1 << 16)
def _day_of_zone(t: float, tz: str) -> str:
    return dt.datetime.fromtimestamp(t, _zone(tz)).date().isoformat()


def day_start(day: str, tz: str | None = None) -> float:
    """Unix time of 00:00 on `day` in `tz`."""
    if tz is None:
        date = dt.date.fromisoformat(day)
        return dt.datetime(date.year, date.month, date.day).timestamp()
    return _day_start_zone(day, tz)


@functools.lru_cache(maxsize=1 << 14)
def _day_start_zone(day: str, tz: str) -> float:
    date = dt.date.fromisoformat(day)
    return dt.datetime(date.year, date.month, date.day, tzinfo=_zone(tz)).timestamp()


def day_end(day: str, tz: str | None = None) -> float:
    """Unix time of 00:00 on the day after `day` (a day can be 23 or 25 h long across a DST change)."""
    return day_start(shift_day(day, 1), tz)


def shift_day(day: str, days: int) -> str:
    return (dt.date.fromisoformat(day) + dt.timedelta(days=days)).isoformat()


def day_range(first: str, last: str) -> list[str]:
    """Every day from `first` to `last`, inclusive ([] when last < first)."""
    start, stop = dt.date.fromisoformat(first), dt.date.fromisoformat(last)
    return [(start + dt.timedelta(days=offset)).isoformat() for offset in range((stop - start).days + 1)]


def parse_time(value, tz: str | None = None) -> float:
    """Unix seconds from a number, a numeric string, or an ISO 8601 string ("Z" allowed; a naive one is read in tz)."""
    if isinstance(value, bool):
        raise ValueError(f"not a time: {value!r}")
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    try:
        return float(text)
    except ValueError:
        pass
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    moment = dt.datetime.fromisoformat(text)
    if moment.tzinfo is None:
        if tz is None:
            return moment.timestamp()
        moment = moment.replace(tzinfo=_zone(tz))
    return moment.timestamp()


# ------------------------------------------------------------------------------------------------------------- records

def _optional_float(value):
    return None if value is None else float(value)


def _record_dict(record) -> dict:
    out = {"schema": SCHEMA}
    for spec in fields(record):
        value = getattr(record, spec.name)
        if value is not None:
            out[spec.name] = value
    return out


def _from_record_dict(cls, data: dict):
    names = {spec.name for spec in fields(cls)}
    return cls(**{key: value for key, value in dict(data).items() if key in names})


@dataclass(frozen=True, kw_only=True)
class Intervention:
    """One time a person had to act so a robot could carry on."""

    id: str                       # unique; ingest is idempotent on it
    t: float                      # when the person was asked, or acted
    robot: str
    level: str                    # one of LEVELS
    cause: str                    # non-empty
    source: str
    reading: str | None = None    # None -> SOURCE_READING.get(source, "asked")
    what: str = ""                # what the person was asked to do or did
    avoidable: str = "unclear"    # one of AVOIDABLE
    fix: str = ""                 # what would remove it, in the labeller's words
    thread: str | None = None     # chat / session / ticket id: repeats in one thread merge into one episode
    by: str | None = None         # who reported it (agent, person, detector)
    human_s: float | None = None  # person-seconds spent, when known
    downtime_s: float | None = None
    commissioning: bool | None = None   # None -> cause in COMMISSIONING_CAUSES
    evidence: str | None = None   # a link, path or short quote

    def __post_init__(self) -> None:
        object.__setattr__(self, "t", float(self.t))
        if self.level not in LEVELS:
            raise ValueError(f"Intervention.level must be one of {LEVELS}, got {self.level!r}")
        if not isinstance(self.cause, str) or not self.cause:
            raise ValueError(f"Intervention.cause must be a non-empty string, got {self.cause!r}")
        reading = self.reading if self.reading is not None else SOURCE_READING.get(self.source, "asked")
        if reading not in READINGS:
            raise ValueError(f"Intervention.reading must be one of {READINGS}, got {reading!r}")
        object.__setattr__(self, "reading", reading)
        if self.avoidable not in AVOIDABLE:
            raise ValueError(f"Intervention.avoidable must be one of {AVOIDABLE}, got {self.avoidable!r}")
        commissioning = self.cause in COMMISSIONING_CAUSES if self.commissioning is None else bool(self.commissioning)
        object.__setattr__(self, "commissioning", commissioning)
        object.__setattr__(self, "human_s", _optional_float(self.human_s))
        object.__setattr__(self, "downtime_s", _optional_float(self.downtime_s))

    def to_dict(self) -> dict:
        """Adds "schema"; reading and commissioning are stored resolved; keys whose value is None are omitted."""
        return _record_dict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Intervention":
        """Ignores unknown keys ("schema" included); round-trips to_dict."""
        return _from_record_dict(cls, d)


@dataclass(frozen=True, kw_only=True)
class Block:
    """An interval in which work on a robot waits on a person (the robot may be busy on something else)."""

    id: str
    robot: str
    cause: str
    why: str
    since_t: float
    until_t: float | None = None  # None = still blocked
    level: str = "remote"         # one of LEVELS
    source: str = "queue"
    item: str | None = None       # the work item that waits
    by: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "since_t", float(self.since_t))
        object.__setattr__(self, "until_t", _optional_float(self.until_t))
        if self.level not in LEVELS:
            raise ValueError(f"Block.level must be one of {LEVELS}, got {self.level!r}")

    def seconds(self, now: float) -> float:
        end = self.until_t if self.until_t is not None else float(now)
        return max(0.0, end - self.since_t)

    def to_dict(self) -> dict:
        return _record_dict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Block":
        return _from_record_dict(cls, d)
