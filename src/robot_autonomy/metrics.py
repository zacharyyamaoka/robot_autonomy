"""The KPI: working hours between interventions (MTBI), the R0-R4 ladder, the causes table and reliability growth.

Stock vocabulary: MTBI is the DMV disengagement report's "miles per disengagement"; the trend is Crow-AMSAA reliability
growth (MIL-HDBK-189); the backlog order is a Pareto of causes (Juran) weighted to the avoidable ones.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from .hours import days_with_hours, per_day, total
from .schema import READINGS, day_end, day_of, day_range, day_start, shift_day


@dataclass(frozen=True)
class Gate:
    level: str
    name: str
    hours: float
    meaning: str


ROBOT_LADDER = (
    Gate("R0", "Bring-up", 0.0, "a person is part of every run; commissioning asks are expected"),
    Gate("R1", "Assisted", 1.0, "at least a working hour between hands-on interventions"),
    Gate("R2", "Shift", 8.0, "a working shift without hands"),
    Gate("R3", "Overnight", 24.0, "a full day and night; every recovery self-served"),
    Gate("R4", "Lights-out", 168.0, "a week without anyone touching the robot"),
)
# WHY these thresholds: each is a stretch of unattended time a person can picture (an hour, a shift, a night,
# a week), not a tuned number (Zach, 2026-11-17).

HUMAN_S_DEFAULT = {"hands_on": 300.0, "remote": 30.0}


def level(ladder, hours: float | None) -> Gate:
    """The highest gate with hours >= gate.hours; None -> ladder[0]."""
    best = ladder[0]
    if hours is None:
        return best
    for gate in ladder:
        if hours >= gate.hours and gate.hours >= best.hours:
            best = gate
    return best


def _rank(ladder, gate: Gate) -> int:
    for index, candidate in enumerate(ladder):
        if candidate == gate:
            return index
    for index, candidate in enumerate(ladder):
        if candidate.level == gate.level:
            return index
    raise ValueError(f"{gate!r} is not on the ladder")


def held(ladder, *hours: float | None) -> Gate:
    """The lowest of level(h) over the readings given.

    WHY the lower wins: the measured agents can game the reading they write (stop asking, relabel); the reading they
    cannot write keeps them honest, so the level is only as good as the worse of the two.
    """
    if not hours:
        return ladder[0]
    return min((level(ladder, value) for value in hours), key=lambda gate: _rank(ladder, gate))


def next_gate(ladder, current: Gate) -> Gate | None:
    index = _rank(ladder, current)
    return ladder[index + 1] if index + 1 < len(ladder) else None


def mtbi(hours: float | None, episodes: int) -> float | None:
    """None when hours is falsy; hours / episodes when episodes > 0; else hours (at least that long)."""
    if not hours:
        return None
    if episodes > 0:
        return hours / episodes
    return hours


@dataclass(frozen=True)
class Reading:
    name: str                   # "asked" | "observed"
    episodes: int               # counted episodes of this reading in the window
    mtbi_h: float | None


@dataclass(frozen=True)
class Kpi:
    start: str                  # the window's first day, inclusive
    end: str                    # the window's last day, inclusive
    working_h: float | None     # None when the window has no hours
    counted: int                # episodes counted toward the KPI, both readings
    hands_on: int
    remote: int
    commissioning: int
    avoidable: int
    unclocked: int
    readings: tuple[Reading, ...]   # one per reading in READINGS, in that order
    mtbi_h: float | None        # the lower of the readings' mtbi_h (None if any is None)
    level: str
    level_name: str
    next_level: str | None
    next_gate_h: float | None
    human_s_per_h: float | None # person-seconds per working hour; None when no hours
    blocked_h: float            # hours of blocks (any robot in scope) overlapping the window


def _robot_set(robots):
    if robots is None:
        return None
    return {robots} if isinstance(robots, str) else set(robots)


def kpi(episodes, hours, *, end: str, days: int = 7, ladder=ROBOT_LADDER, count_levels=("hands_on",), robots=None,
        blocks=(), now: float | None = None, human_s_default=HUMAN_S_DEFAULT, tz=None) -> Kpi:
    """The KPI over the `days` days ending on `end` (inclusive). Rules: docs/SPEC.md, "Rules for kpi"."""
    robots = _robot_set(robots)
    start = shift_day(end, -(days - 1))
    window = set(day_range(start, end))
    # WHY divide by working hours: activity is the counterweight. An idle robot never needs a hand, so only hours
    # the robot actually worked (the Robot Activity Agent's activity hours by default) can earn the KPI.
    working = total(hours, robots=robots, days=window)
    working_h = working if working > 0 else None
    clocked_days = days_with_hours(hours, robots=robots)

    counted_by_reading = {name: 0 for name in READINGS}
    counted = hands_on = remote = commissioning = avoidable = unclocked = 0
    human_s = 0.0
    for episode in episodes:
        if robots is not None and episode.robot not in robots:
            continue
        day = day_of(episode.first_t, tz)
        if day not in window:
            continue
        # WHY clocked: a ratio may only divide events the hours clock also saw.
        if day not in clocked_days:
            unclocked += 1
            continue
        if episode.level == "hands_on":
            hands_on += 1
        elif episode.level == "remote":
            remote += 1
        if episode.commissioning:
            commissioning += 1
        if episode.avoidable == "yes":
            avoidable += 1
        human_s += episode.human_s if episode.human_s is not None else human_s_default.get(episode.level, 0.0)
        if episode.level in count_levels and not episode.commissioning:
            counted += 1
            counted_by_reading[episode.reading] = counted_by_reading.get(episode.reading, 0) + 1

    readings = tuple(Reading(name, counted_by_reading[name], mtbi(working_h, counted_by_reading[name]))
                     for name in READINGS)
    values = [reading.mtbi_h for reading in readings]
    mtbi_h = None if any(value is None for value in values) else min(values)
    gate = held(ladder, *values)
    upcoming = next_gate(ladder, gate)

    window_start, window_end = day_start(start, tz), day_end(end, tz)
    open_until = window_end if now is None else float(now)
    blocked_s = 0.0
    for block in blocks:
        if robots is not None and block.robot not in robots:
            continue
        block_end = block.until_t if block.until_t is not None else open_until
        blocked_s += max(0.0, min(block_end, window_end) - max(block.since_t, window_start))

    return Kpi(start=start, end=end, working_h=working_h, counted=counted, hands_on=hands_on, remote=remote,
               commissioning=commissioning, avoidable=avoidable, unclocked=unclocked, readings=readings,
               mtbi_h=mtbi_h, level=gate.level, level_name=gate.name,
               next_level=upcoming.level if upcoming else None, next_gate_h=upcoming.hours if upcoming else None,
               human_s_per_h=human_s / working_h if working_h else None, blocked_h=blocked_s / 3600.0)


# ------------------------------------------------------------------------------------------------------------- causes

@dataclass(frozen=True)
class Cause:
    level: str
    cause: str
    episodes: int
    avoidable: int
    share: float                # episodes / all episodes in the table
    fixes: tuple[str, ...]      # distinct fixes, most frequent first (ties: first seen), at most 8
    recent: int                 # episodes with first_t >= now - recent_days
    last_t: float
    commissioning: bool         # every episode of the cause is commissioning
    asked: int = 0              # of `episodes`, the ones on the asked reading
    observed: int = 0           # ... and on the observed reading. WHY shown apart: the two readings often see the
                                # same real event (an ask, then the person's own "done"), so `episodes` can count it twice


def causes(episodes, *, since=None, until=None, now=None, recent_days: float = 7) -> list[Cause]:
    """Episodes grouped by (level, cause), since <= first_t < until; sorted by (-episodes, -avoidable, level, cause)."""
    groups: dict[tuple[str, str], list] = {}
    for episode in sorted(episodes, key=lambda episode: (episode.first_t, episode.key)):
        if since is not None and episode.first_t < since:
            continue
        if until is not None and episode.first_t >= until:
            continue
        groups.setdefault((episode.level, episode.cause), []).append(episode)
    if not groups:
        return []
    if now is None:
        # SPEC clarification 2: with no `now`, "recent" is anchored on the latest last_t in the table.
        now = max(episode.last_t for group in groups.values() for episode in group)
    recent_from = float(now) - recent_days * 86400.0
    total_episodes = sum(len(group) for group in groups.values())
    rows = []
    for (level_name, cause_name), group in groups.items():
        fix_counts: Counter = Counter()
        first_seen: dict[str, int] = {}
        for episode in group:
            for fix in episode.fixes:
                fix_counts[fix] += 1
                first_seen.setdefault(fix, len(first_seen))
        fixes = sorted(fix_counts, key=lambda fix: (-fix_counts[fix], first_seen[fix]))[:8]
        rows.append(Cause(level=level_name, cause=cause_name, episodes=len(group),
                          avoidable=sum(1 for episode in group if episode.avoidable == "yes"),
                          share=len(group) / total_episodes, fixes=tuple(fixes),
                          recent=sum(1 for episode in group if episode.first_t >= recent_from),
                          last_t=max(episode.last_t for episode in group),
                          commissioning=all(episode.commissioning for episode in group),
                          asked=sum(1 for episode in group if episode.reading == "asked"),
                          observed=sum(1 for episode in group if episode.reading == "observed")))
    rows.sort(key=lambda row: (-row.episodes, -row.avoidable, row.level, row.cause))
    return rows


def backlog_order(rows: list[Cause]) -> list[Cause]:
    """The order the agent works in: avoidable > 0 only, sorted by (-avoidable, -episodes, -recent, cause)."""
    return sorted((row for row in rows if row.avoidable > 0),
                  key=lambda row: (-row.avoidable, -row.episodes, -row.recent, row.cause))


# ------------------------------------------------------------------------------------------------------------ growth

@dataclass(frozen=True)
class Growth:                    # Crow-AMSAA (power-law NHPP), time-terminated MLE
    n: int
    total_h: float
    beta: float
    lam: float
    mtbi_now_h: float            # instantaneous: 1 / (lam * beta * total_h ** (beta - 1))
    mtbi_avg_h: float            # total_h / n
    improving: bool              # beta < 1


def growth(event_h, total_h: float) -> Growth | None:
    """Crow-AMSAA fit. event_h: cumulative working hours at each event (0 < t_i <= total_h; others dropped).
    beta = n / sum(ln(total_h / t_i)), lam = n / total_h ** beta. None when n < 2 or the sum is 0."""
    total_h = float(total_h)
    times = [float(t) for t in event_h if 0.0 < float(t) <= total_h]
    count = len(times)
    if count < 2:
        return None
    log_sum = sum(math.log(total_h / t) for t in times)
    if log_sum == 0:
        return None
    beta = count / log_sum
    lam = count / total_h ** beta
    mtbi_now_h = 1.0 / (lam * beta * total_h ** (beta - 1.0))
    return Growth(n=count, total_h=total_h, beta=beta, lam=lam, mtbi_now_h=mtbi_now_h,
                  mtbi_avg_h=total_h / count, improving=beta < 1.0)


def cumulative_hours(episodes, hours, *, robots=None, tz=None) -> tuple[list[float], float]:
    """Each episode's cumulative working hours at first_t: every earlier day's hours plus that day's hours times the
    fraction of the day elapsed at first_t (in tz). Returns (per-episode list in input order, total)."""
    robots = _robot_set(robots)
    hours_by_day = per_day(hours, robots=robots)
    days_sorted = sorted(hours_by_day)
    before_day: dict[str, float] = {}
    running = 0.0
    for day in days_sorted:
        before_day[day] = running
        running += hours_by_day[day]
    total_h = running

    def hours_before(day: str) -> float:
        if day in before_day:
            return before_day[day]
        return sum(hours_by_day[earlier] for earlier in days_sorted if earlier < day)

    out = []
    for episode in episodes:
        # SPEC: robots filters the episodes too (as in kpi), so the list holds the in-scope episodes, input order.
        if robots is not None and episode.robot not in robots:
            continue
        day = day_of(episode.first_t, tz)
        start, stop = day_start(day, tz), day_end(day, tz)
        fraction = min(1.0, max(0.0, (episode.first_t - start) / (stop - start)))
        out.append(hours_before(day) + hours_by_day.get(day, 0.0) * fraction)
    return out, total_h


def daily(episodes, hours, *, window_days=7, count_levels=("hands_on",), ladder=ROBOT_LADDER, robots=None,
          tz=None) -> list[dict]:
    """One row per day from the first day with hours or episodes to the last: {"day", "working_h", "episodes"
    (counted that day), "mtbi_h" and "level" of the window ending that day}."""
    robots = _robot_set(robots)
    episodes = [episode for episode in episodes if robots is None or episode.robot in robots]
    hours_by_day = per_day(hours, robots=robots)
    days = {day for day, value in hours_by_day.items() if value > 0}
    days |= {day_of(episode.first_t, tz) for episode in episodes}
    if not days:
        return []
    rows = []
    for day in day_range(min(days), max(days)):
        day_kpi = kpi(episodes, hours, end=day, days=1, ladder=ladder, count_levels=count_levels, robots=robots,
                      tz=tz)
        window_kpi = kpi(episodes, hours, end=day, days=window_days, ladder=ladder, count_levels=count_levels,
                         robots=robots, tz=tz)
        rows.append({"day": day, "working_h": hours_by_day.get(day, 0.0), "episodes": day_kpi.counted,
                     "mtbi_h": window_kpi.mtbi_h, "level": window_kpi.level})
    return rows
