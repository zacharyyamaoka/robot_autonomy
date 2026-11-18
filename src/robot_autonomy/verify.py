"""Did the fix work? An exact conditional test of two Poisson rates: the cause's rate before the fix vs after it.

WHY verification by measurement only: a fix is "verified" when its cause measurably went down in working hours after
it landed, never because someone (or some agent) says it is done.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from .hours import days_with_hours, per_day, total
from .schema import READINGS, day_of, day_range, shift_day

VERDICTS = ("verified", "ineffective", "pending")


@dataclass(frozen=True)
class Rate:
    episodes: int
    hours: float
    per_100h: float | None      # 100 * episodes / hours; None when hours == 0


@dataclass(frozen=True)
class Effect:
    before: Rate
    after: Rate
    p_value: float | None       # None when there is nothing to test
    drop: float | None          # 1 - after.per_100h / before.per_100h; None when either rate is None or before is 0
    verdict: str                # "verified" | "ineffective" | "pending"
    reading: str = "asked"      # the one reading both sides were counted on
    look: int | None = None     # which look (0, 1, 2, ...): the after side spans min_after_h * 2**look hours
    alpha: float | None = None  # the alpha this look spends: alpha / 2**(look + 1)

    def to_dict(self) -> dict:
        return {"before": asdict(self.before), "after": asdict(self.after), "p_value": self.p_value,
                "drop": self.drop, "verdict": self.verdict, "reading": self.reading, "look": self.look,
                "alpha": self.alpha}


def _rate(episodes: int, hours: float) -> Rate:
    return Rate(episodes=episodes, hours=hours, per_100h=100.0 * episodes / hours if hours else None)


def _look(after_days: list[str], hours_by_day: dict[str, float], min_after_h: float) -> tuple[int | None, list[str]]:
    """(look index, the after-days prefix it uses). (None, every after day) before the first horizon is reached."""
    cumulative, reached = [], 0.0
    for day in after_days:
        reached += hours_by_day.get(day, 0.0)
        cumulative.append(reached)
    if min_after_h <= 0 or reached < min_after_h:
        return None, list(after_days)
    look = 0
    while reached >= min_after_h * 2 ** (look + 1):
        look += 1
    horizon = min_after_h * 2 ** look
    end = next(index for index, value in enumerate(cumulative) if value >= horizon)
    return look, list(after_days[: end + 1])


def rate_test(n_before: int, h_before: float, n_after: int, h_after: float) -> float:
    """One-sided exact conditional test that the after-rate is lower.

    Given n = n_before + n_after events, X (the after count) ~ Binomial(n, h_after / (h_before + h_after)) when the
    rate did not change; returns P(X <= n_after). 1.0 when n == 0.
    """
    n_total = int(n_before) + int(n_after)
    n_after = int(n_after)
    if n_total == 0:
        return 1.0
    hours_total = float(h_before) + float(h_after)
    if hours_total <= 0:
        return 1.0
    share_after = float(h_after) / hours_total
    if share_after <= 0.0:
        return 1.0                      # X is always 0
    if share_after >= 1.0:
        return 1.0 if n_after >= n_total else 0.0
    log_p, log_q = math.log(share_after), math.log1p(-share_after)
    log_n_factorial = math.lgamma(n_total + 1)
    cumulative = 0.0
    for k in range(0, min(n_after, n_total) + 1):
        log_pmf = (log_n_factorial - math.lgamma(k + 1) - math.lgamma(n_total - k + 1)
                   + k * log_p + (n_total - k) * log_q)
        cumulative += math.exp(log_pmf)
    return min(1.0, max(0.0, cumulative))


def assess(causes, done_t: float, episodes, hours, *, now: float, robots=None, baseline_days=28, min_after_h=24.0,
           alpha=0.1, count_levels=("hands_on", "remote"), tz=None, reading: str | None = None) -> Effect:
    """Before = the baseline_days days ending the day before the done day; after = the complete days after the done
    day up to yesterday (day_of(now) - 1). The done day itself is in neither. Verdict rules: docs/SPEC.md.

    WHY one reading: the two readings often see the same real event twice (an agent asks for a replug, and the
    person then logs the replug). Counting both makes the exact Poisson test over-confident: in the demo, seed 4
    "verified" a fix that did nothing (p = 0.09). `reading=None` picks the reading with the most before-window
    episodes (ties go to READINGS order), so the choice is made before the after window is looked at."""
    cause_set = {causes} if isinstance(causes, str) else set(causes)
    robot_set = None if robots is None else ({robots} if isinstance(robots, str) else set(robots))
    done_day = day_of(done_t, tz)
    before_days = set(day_range(shift_day(done_day, -baseline_days), shift_day(done_day, -1)))
    after_all = day_range(shift_day(done_day, 1), shift_day(day_of(now, tz), -1))
    # WHY looks at doubling horizons (group-sequential, Bonferroni alpha spending): the agent re-runs this every day,
    # and stopping at the first p < alpha inflates false "verified" far past alpha (optional stopping). Look k uses
    # the shortest prefix of after-days whose hours reach min_after_h * 2**k, and alpha / 2**(k + 1); the looks'
    # alphas sum to alpha. Between two horizons the prefix is the same, so a daily re-run is not a new look.
    look, after_list = _look(after_all, per_day(hours, robots=robot_set), min_after_h)
    after_days = set(after_list)
    clocked = days_with_hours(hours, robots=robot_set)

    counts = {name: [0, 0] for name in READINGS}  # reading -> [before, after]
    for episode in episodes:
        if episode.cause not in cause_set or episode.level not in count_levels:
            continue
        if robot_set is not None and episode.robot not in robot_set:
            continue
        day = day_of(episode.first_t, tz)
        if day not in clocked:
            continue
        side = 0 if day in before_days else 1 if day in after_days else None
        if side is not None:
            counts.setdefault(episode.reading, [0, 0])[side] += 1
    if reading is None:
        reading = max(READINGS, key=lambda name: (counts[name][0], -READINGS.index(name)))
    n_before, n_after = counts.get(reading, [0, 0])

    before = _rate(n_before, total(hours, robots=robot_set, days=before_days))
    after = _rate(n_after, total(hours, robots=robot_set, days=after_days))
    # SPEC: "nothing to test" = either side has no hours, or no episodes on either side.
    if before.hours > 0 and after.hours > 0 and n_before + n_after > 0:
        p_value = rate_test(n_before, before.hours, n_after, after.hours)
    else:
        p_value = None
    drop = None
    if before.per_100h is not None and after.per_100h is not None and before.per_100h != 0:
        drop = 1.0 - after.per_100h / before.per_100h

    look_alpha = None if look is None else alpha / 2 ** (look + 1)
    if look is None or after.hours < min_after_h:
        verdict = "pending"
    elif before.episodes == 0:
        # WHY: no drop can be shown from 0, so a quiet cause stays pending; one that shows up after it is not fixed.
        verdict = "ineffective" if after.episodes > 0 else "pending"
    elif p_value is not None and p_value < look_alpha:
        verdict = "verified"
    elif (after.per_100h is not None and before.per_100h is not None and after.per_100h >= before.per_100h
          and before.per_100h * after.hours / 100.0 >= 3):
        verdict = "ineffective"
    else:
        verdict = "pending"
    return Effect(before=before, after=after, p_value=p_value, drop=drop, verdict=verdict, reading=reading,
                  look=look, alpha=look_alpha)
