"""Did the fix work? An exact conditional test of two Poisson rates: the cause's rate before the fix vs after it.

WHY verification by measurement only: a fix is "verified" when its cause measurably went down in working hours after
it landed, never because someone (or some agent) says it is done.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from .hours import days_with_hours, total
from .schema import day_of, day_range, shift_day

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

    def to_dict(self) -> dict:
        return {"before": asdict(self.before), "after": asdict(self.after), "p_value": self.p_value,
                "drop": self.drop, "verdict": self.verdict}


def _rate(episodes: int, hours: float) -> Rate:
    return Rate(episodes=episodes, hours=hours, per_100h=100.0 * episodes / hours if hours else None)


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
           alpha=0.1, count_levels=("hands_on", "remote"), tz=None) -> Effect:
    """Before = the baseline_days days ending the day before the done day; after = the complete days after the done
    day up to yesterday (day_of(now) - 1). The done day itself is in neither. Verdict rules: docs/SPEC.md."""
    cause_set = {causes} if isinstance(causes, str) else set(causes)
    robot_set = None if robots is None else ({robots} if isinstance(robots, str) else set(robots))
    done_day = day_of(done_t, tz)
    before_days = set(day_range(shift_day(done_day, -baseline_days), shift_day(done_day, -1)))
    after_days = set(day_range(shift_day(done_day, 1), shift_day(day_of(now, tz), -1)))
    clocked = days_with_hours(hours, robots=robot_set)

    n_before = n_after = 0
    for episode in episodes:
        if episode.cause not in cause_set or episode.level not in count_levels:
            continue
        if robot_set is not None and episode.robot not in robot_set:
            continue
        day = day_of(episode.first_t, tz)
        if day not in clocked:
            continue
        if day in before_days:
            n_before += 1
        elif day in after_days:
            n_after += 1

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

    if after.hours < min_after_h:
        verdict = "pending"
    elif before.episodes == 0:
        # WHY: no drop can be shown from 0, so a quiet cause stays pending; one that shows up after it is not fixed.
        verdict = "ineffective" if after.episodes > 0 else "pending"
    elif p_value is not None and p_value < alpha:
        verdict = "verified"
    elif (after.per_100h is not None and before.per_100h is not None and after.per_100h >= before.per_100h
          and before.per_100h * after.hours / 100.0 >= 3):
        verdict = "ineffective"
    else:
        verdict = "pending"
    return Effect(before=before, after=after, p_value=p_value, drop=drop, verdict=verdict)
