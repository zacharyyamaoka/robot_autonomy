"""verify.py: did the fix work? (SPEC "verify.py — did the fix work?")."""

from __future__ import annotations

import json
import math

import pytest

from helpers import day_range, make_episode, t_of
from robot_autonomy import verify
from robot_autonomy.verify import Effect, Rate


def binomial_cdf(successes: int, trials: int, probability: float) -> float:
    """P(X <= successes) for X ~ Binomial(trials, probability), summed exactly."""
    return sum(
        math.comb(trials, k) * probability**k * (1.0 - probability) ** (trials - k) for k in range(successes + 1)
    )


# --------------------------------------------------------------------------------------------- rate_test


def test_rate_test_all_before_none_after():
    # n = 9, p_after = 50 / 150 = 1/3: P(X <= 0) = (2/3) ** 9 = (100 / 150) ** 9
    assert verify.rate_test(9, 100.0, 0, 50.0) == pytest.approx((100.0 / 150.0) ** 9)


def test_rate_test_equal_hours():
    # X ~ Bin(8, 1/2): P(X <= 3) = (1 + 8 + 28 + 56) / 256 = 93 / 256
    assert verify.rate_test(5, 100.0, 3, 100.0) == pytest.approx(93.0 / 256.0)
    assert verify.rate_test(5, 100.0, 3, 100.0) == pytest.approx(binomial_cdf(3, 8, 0.5))


def test_rate_test_uneven_hours():
    # X ~ Bin(8, 30 / 40): P(X <= 6)
    assert verify.rate_test(2, 10.0, 6, 30.0) == pytest.approx(binomial_cdf(6, 8, 0.75))


def test_rate_test_no_events_is_one():
    assert verify.rate_test(0, 10.0, 0, 10.0) == 1.0


def test_rate_test_all_after_is_one():
    # X ~ Bin(4, 1/2): P(X <= 4) = 1
    assert verify.rate_test(0, 10.0, 4, 10.0) == pytest.approx(1.0)


# --------------------------------------------------------------------------------------------- Rate


def test_rate_per_100h():
    assert verify.Rate(episodes=3, hours=30.0, per_100h=10.0).per_100h == 10.0  # plain dataclass
    effect_rate = Rate(episodes=0, hours=0.0, per_100h=None)
    assert effect_rate.per_100h is None


# --------------------------------------------------------------------------------------------- assess

# Hours: arm-1 has 10 h every day from 2026-11-01 to 2026-11-20 except 2026-11-08 (no hours).
HOURS = {("arm-1", day): 10.0 for day in day_range("2026-11-01", "2026-11-20") if day != "2026-11-08"}
DONE_T = t_of("2026-11-10", 14.0)  # D = 2026-11-10
# baseline_days=4 -> before = 2026-11-06 .. 2026-11-09 -> hours 10 + 10 + 0 + 10 = 30
NOW = t_of("2026-11-14", 6.0)  # after = 2026-11-11 .. 2026-11-13 -> hours 30
CAUSES = ("power_or_replug",)


def power(day: str, hour: float, *, level: str = "hands_on", cause: str = "power_or_replug", robot: str = "arm-1"):
    return make_episode(f"{robot}-{cause}-{level}-{day}-{hour}", t_of(day, hour), level=level, cause=cause, robot=robot)


def _verified_case():
    counted_before = [power(day, hour) for day in ("2026-11-06", "2026-11-07", "2026-11-09") for hour in (1, 2, 3)]
    ignored = [
        power("2026-11-08", 1),  # unclocked (no hours that day)
        power("2026-11-08", 2),
        power("2026-11-05", 1),  # before the baseline
        power("2026-11-10", 15),  # the done day itself
        power("2026-11-07", 5, cause="belt_or_items"),  # another cause
        power("2026-11-14", 1),  # the day of `now`: incomplete, not in after
        power("2026-11-14", 2),
    ]
    remote = [power("2026-11-07", 6, level="remote")]  # counted only when count_levels includes "remote"
    return counted_before + ignored + remote


def test_assess_verified():
    effect = verify.assess(
        CAUSES, DONE_T, _verified_case(), HOURS, now=NOW, baseline_days=4, count_levels=("hands_on",), tz="UTC"
    )
    assert isinstance(effect, Effect)
    assert effect.before.episodes == 9
    assert effect.before.hours == pytest.approx(30.0)
    assert effect.before.per_100h == pytest.approx(30.0)  # 100 * 9 / 30
    assert effect.after.episodes == 0
    assert effect.after.hours == pytest.approx(30.0)
    assert effect.after.per_100h == pytest.approx(0.0)
    # X ~ Bin(9, 30 / 60): P(X <= 0) = 0.5 ** 9 = 1 / 512 < alpha 0.1
    assert effect.p_value == pytest.approx(0.5**9)
    assert effect.drop == pytest.approx(1.0)  # 1 - 0 / 30
    assert effect.verdict == "verified"


def test_assess_default_count_levels_include_remote():
    effect = verify.assess(CAUSES, DONE_T, _verified_case(), HOURS, now=NOW, baseline_days=4, tz="UTC")
    assert effect.before.episodes == 10  # + the remote one
    assert effect.p_value == pytest.approx(0.5**10)
    assert effect.verdict == "verified"


def test_assess_counts_every_cause_given():
    effect = verify.assess(
        ("power_or_replug", "belt_or_items"), DONE_T, _verified_case(), HOURS, now=NOW, baseline_days=4,
        count_levels=("hands_on",), tz="UTC",
    )
    assert effect.before.episodes == 10  # + the belt one


def test_assess_alpha_is_honoured():
    # p = 1/512 ~= 0.00195 > alpha 0.001; after (0) is lower than before, so rule 4 fails too -> pending
    effect = verify.assess(
        CAUSES, DONE_T, _verified_case(), HOURS, now=NOW, baseline_days=4, count_levels=("hands_on",), alpha=0.001,
        tz="UTC",
    )
    assert effect.verdict == "pending"


def test_assess_pending_until_enough_after_hours():
    # now on 2026-11-12: after = 2026-11-11 only = 10 h < min_after_h 24
    effect = verify.assess(
        CAUSES, DONE_T, _verified_case(), HOURS, now=t_of("2026-11-12", 6.0), baseline_days=4,
        count_levels=("hands_on",), tz="UTC",
    )
    assert effect.after.hours == pytest.approx(10.0)
    assert effect.verdict == "pending"
    # with min_after_h=5 the same data is tested: X ~ Bin(9, 10 / 40): P(X <= 0) = 0.75 ** 9 ~= 0.075 < 0.1
    effect = verify.assess(
        CAUSES, DONE_T, _verified_case(), HOURS, now=t_of("2026-11-12", 6.0), baseline_days=4, min_after_h=5.0,
        count_levels=("hands_on",), tz="UTC",
    )
    assert effect.p_value == pytest.approx(0.75**9)
    assert effect.verdict == "verified"


def test_assess_with_no_after_days():
    # now on 2026-11-11: the after side is empty (no complete day after D yet)
    effect = verify.assess(
        CAUSES, DONE_T, _verified_case(), HOURS, now=t_of("2026-11-11", 6.0), baseline_days=4, tz="UTC"
    )
    assert effect.after.episodes == 0
    assert effect.after.hours == 0.0
    assert effect.after.per_100h is None
    assert effect.drop is None
    assert effect.verdict == "pending"


def test_assess_nothing_before_and_something_after_is_ineffective():
    episodes = [power("2026-11-11", 1)]
    effect = verify.assess(CAUSES, DONE_T, episodes, HOURS, now=NOW, baseline_days=4, tz="UTC")
    assert effect.before.episodes == 0
    assert effect.after.episodes == 1
    assert effect.drop is None  # before is 0
    assert effect.verdict == "ineffective"


def test_assess_nothing_before_and_nothing_after_is_pending():
    effect = verify.assess(CAUSES, DONE_T, [], HOURS, now=NOW, baseline_days=4, tz="UTC")
    assert effect.before.episodes == 0
    assert effect.after.episodes == 0
    assert effect.verdict == "pending"


def test_assess_rate_did_not_fall_with_enough_expected_events_is_ineffective():
    # before 3 in 30 h (10 / 100 h); after 4 in 30 h (13.3 / 100 h)
    # p = P(X <= 4), X ~ Bin(7, 1/2) = 99 / 128 > 0.1; after >= before; expected after = 10 * 30 / 100 = 3 >= 3
    episodes = [power("2026-11-06", 1), power("2026-11-07", 1), power("2026-11-09", 1)] + [
        power("2026-11-11", 1), power("2026-11-11", 2), power("2026-11-12", 1), power("2026-11-13", 1)
    ]
    effect = verify.assess(CAUSES, DONE_T, episodes, HOURS, now=NOW, baseline_days=4, tz="UTC")
    assert effect.p_value == pytest.approx(99.0 / 128.0)
    assert effect.before.per_100h == pytest.approx(10.0)
    assert effect.after.per_100h == pytest.approx(400.0 / 30.0)
    assert effect.drop == pytest.approx(1.0 - (400.0 / 30.0) / 10.0)
    assert effect.verdict == "ineffective"


def test_assess_rate_did_not_fall_but_too_few_expected_events_is_pending():
    # before 2 in 30 h, after 2 in 30 h: after >= before, but expected = (200 / 30) * 30 / 100 = 2 < 3
    episodes = [power("2026-11-06", 1), power("2026-11-07", 1), power("2026-11-11", 1), power("2026-11-12", 1)]
    effect = verify.assess(CAUSES, DONE_T, episodes, HOURS, now=NOW, baseline_days=4, tz="UTC")
    assert effect.p_value == pytest.approx(binomial_cdf(2, 4, 0.5))  # 11 / 16
    assert effect.verdict == "pending"


def test_assess_rate_fell_but_not_significantly_is_pending():
    # before 3, after 1: p = P(X <= 1), X ~ Bin(4, 1/2) = 5 / 16 > 0.1; after < before
    episodes = [power("2026-11-06", 1), power("2026-11-07", 1), power("2026-11-09", 1), power("2026-11-12", 1)]
    effect = verify.assess(CAUSES, DONE_T, episodes, HOURS, now=NOW, baseline_days=4, tz="UTC")
    assert effect.p_value == pytest.approx(5.0 / 16.0)
    assert effect.drop == pytest.approx(1.0 - (100.0 / 30.0) / 10.0)
    assert effect.verdict == "pending"


def test_assess_robots_filter_applies_to_hours_and_episodes():
    hours = dict(HOURS)
    hours.update({("arm-2", day): 10.0 for day in day_range("2026-11-01", "2026-11-20")})
    episodes = _verified_case() + [power("2026-11-11", hour, robot="arm-2") for hour in (1, 2, 3)]
    effect = verify.assess(
        CAUSES, DONE_T, episodes, hours, now=NOW, robots=["arm-1"], baseline_days=4, count_levels=("hands_on",),
        tz="UTC",
    )
    assert effect.before.episodes == 9
    assert effect.before.hours == pytest.approx(30.0)
    assert effect.after.episodes == 0
    assert effect.after.hours == pytest.approx(30.0)
    assert effect.verdict == "verified"


def test_assess_default_baseline_is_28_days():
    hours = {("arm-1", day): 10.0 for day in day_range("2026-10-01", "2026-11-20")}
    # before = 2026-10-13 .. 2026-11-09 (28 days, 280 h); one episode on 2026-10-13 (inside), one on 10-12 (outside)
    episodes = [power("2026-10-13", 1), power("2026-10-12", 1)]
    effect = verify.assess(CAUSES, DONE_T, episodes, hours, now=NOW, tz="UTC")
    assert effect.before.hours == pytest.approx(280.0)
    assert effect.before.episodes == 1


def test_effect_to_dict_is_json():
    effect = verify.assess(
        CAUSES, DONE_T, _verified_case(), HOURS, now=NOW, baseline_days=4, count_levels=("hands_on",), tz="UTC"
    )
    as_dict = effect.to_dict()
    assert isinstance(as_dict, dict)
    json.loads(json.dumps(as_dict))
