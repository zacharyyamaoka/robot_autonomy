"""Verification that cannot be fooled by the two readings or by being re-run every day (SPEC: verify.py, looks).

Added 2026-11-18 after the demo's do-nothing fix was "verified" in one seed of sixteen: each real event was counted
by both readings, and the agent re-tested daily and stopped at the first p < alpha.
"""

from __future__ import annotations

import random

import pytest

from helpers import day_range, make_episode, t_of

from robot_autonomy import verify

CAUSES = ("power_or_replug",)
DONE_T = t_of("2026-11-10", 14.0)  # before (baseline_days=4) = 11-06 .. 11-09; after starts 11-11


def episode(day, hour, reading, *, ident=None):
    return make_episode(ident or f"{reading}-{day}-{hour}", t_of(day, hour), level="hands_on", cause=CAUSES[0],
                        robot="arm-1", reading=reading)


def hours_every_day(first="2026-10-01", last="2026-12-31", per_day=10.0):
    return {("arm-1", day): per_day for day in day_range(first, last)}


def test_one_reading_is_counted_not_both():
    # The same 6 real events before the fix, each seen by both readings: 6 asked + 6 observed episodes.
    before = [episode(day, hour, reading) for day in ("2026-11-06", "2026-11-07") for hour in (1, 2, 3)
              for reading in ("asked", "observed")]
    effect = verify.assess(CAUSES, DONE_T, before, hours_every_day(), now=t_of("2026-11-14", 6.0), baseline_days=4,
                           tz="UTC")
    assert effect.before.episodes == 6
    assert effect.reading == "asked"  # tie -> READINGS order


def test_the_reading_with_more_before_episodes_is_chosen():
    before = [episode("2026-11-06", hour, "observed") for hour in (1, 2, 3)] + [episode("2026-11-07", 1, "asked")]
    effect = verify.assess(CAUSES, DONE_T, before, hours_every_day(), now=t_of("2026-11-14", 6.0), baseline_days=4,
                           tz="UTC")
    assert effect.reading == "observed"
    assert effect.before.episodes == 3


def test_an_explicit_reading_is_honoured():
    before = [episode("2026-11-06", hour, "observed") for hour in (1, 2, 3)] + [episode("2026-11-07", 1, "asked")]
    effect = verify.assess(CAUSES, DONE_T, before, hours_every_day(), now=t_of("2026-11-14", 6.0), baseline_days=4,
                           tz="UTC", reading="asked")
    assert effect.reading == "asked"
    assert effect.before.episodes == 1


def test_looks_use_a_fixed_prefix_between_horizons():
    # 10 h per after-day, min_after_h 24: look 0 needs 3 days (30 h); look 1 needs 48 h -> 5 days.
    events = [episode("2026-11-06", hour, "asked") for hour in (1, 2, 3, 4, 5)]
    events += [episode("2026-11-14", 1, "asked")]  # on the 4th after-day: outside look 0's prefix
    hours = hours_every_day()
    on_day_4 = verify.assess(CAUSES, DONE_T, events, hours, now=t_of("2026-11-15", 6.0), baseline_days=4, tz="UTC")
    on_day_3 = verify.assess(CAUSES, DONE_T, events, hours, now=t_of("2026-11-14", 6.0), baseline_days=4, tz="UTC")
    assert on_day_3.look == on_day_4.look == 0
    assert on_day_4.after.hours == pytest.approx(30.0) == on_day_3.after.hours
    assert on_day_4.after.episodes == 0  # the 11-14 event waits for look 1
    assert on_day_4.to_dict() == on_day_3.to_dict()  # a daily re-run between horizons is not a new look
    on_day_5 = verify.assess(CAUSES, DONE_T, events, hours, now=t_of("2026-11-16", 6.0), baseline_days=4, tz="UTC")
    assert on_day_5.look == 1
    assert on_day_5.alpha == pytest.approx(0.025)
    assert on_day_5.after.hours == pytest.approx(50.0)
    assert on_day_5.after.episodes == 1


def _poisson(rng, mean):
    count, product, limit = 0, rng.random(), pow(2.718281828459045, -mean)
    while product > limit:
        count += 1
        product *= rng.random()
    return count


def test_a_fix_that_does_nothing_is_rarely_verified_under_daily_re_runs():
    """Monte Carlo of the agent's real use: a fix with no effect, assessed every day for 28 days, stopping at the
    first verdict. The false-verified rate must stay at or under alpha (0.1); 400 trials, fixed seed."""
    rng = random.Random(20261118)
    hours = hours_every_day("2026-10-01", "2026-12-31", per_day=8.0)
    rate_per_h = 0.15
    trials, verified = 400, 0
    for trial in range(trials):
        events = []
        for day in day_range("2026-10-13", "2026-12-08"):
            for index in range(_poisson(rng, rate_per_h * 8.0)):
                hour = 8.0 + 8.0 * rng.random()
                events.append(episode(day, hour, "asked", ident=f"{trial}-{day}-{index}"))
        for day in day_range("2026-11-12", "2026-12-09"):
            effect = verify.assess(CAUSES, DONE_T, events, hours, now=t_of(day, 6.0), tz="UTC")
            if effect.verdict != "pending":
                verified += effect.verdict == "verified"
                break
    assert verified / trials <= 0.1
