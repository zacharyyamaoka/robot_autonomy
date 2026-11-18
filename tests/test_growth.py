"""metrics.growth and metrics.cumulative_hours: Crow-AMSAA reliability growth (SPEC "metrics.py", Growth)."""

from __future__ import annotations

import math

import pytest

from helpers import make_episode, t_of
from robot_autonomy import metrics
from robot_autonomy.metrics import Growth


def test_growth_by_hand():
    # event_h = [2, 8], total_h = 16, n = 2
    # sum ln(T / t_i) = ln(16 / 2) + ln(16 / 8) = ln 8 + ln 2 = ln 16
    # beta = 2 / ln 16 = 1 / (2 ln 2) ~= 0.7213
    # lam = n / T ** beta = 2 / 16 ** (2 / ln 16) = 2 / e ** 2   (since beta * ln 16 = 2)
    # mtbi_now = 1 / (lam * beta * T ** (beta - 1)) = T / (n * beta) = 16 / (2 * 2 / ln 16) = 4 ln 16 ~= 11.09
    # mtbi_avg = 16 / 2 = 8
    result = metrics.growth([2.0, 8.0], 16.0)
    assert isinstance(result, Growth)
    assert result.n == 2
    assert result.total_h == pytest.approx(16.0)
    assert result.beta == pytest.approx(2.0 / math.log(16.0))
    assert result.lam == pytest.approx(2.0 / math.e**2)
    assert result.mtbi_now_h == pytest.approx(4.0 * math.log(16.0))
    assert result.mtbi_avg_h == pytest.approx(8.0)
    assert result.improving is True  # beta < 1


def test_growth_formula_on_an_uneven_sample():
    event_h = [1.0, 3.0, 4.5, 10.0, 25.0]
    total_h = 40.0
    log_sum = sum(math.log(total_h / event) for event in event_h)
    beta = len(event_h) / log_sum
    lam = len(event_h) / total_h**beta
    result = metrics.growth(event_h, total_h)
    assert result.beta == pytest.approx(beta)
    assert result.lam == pytest.approx(lam)
    assert result.mtbi_now_h == pytest.approx(1.0 / (lam * beta * total_h ** (beta - 1.0)))
    assert result.mtbi_avg_h == pytest.approx(total_h / 5)


def test_growth_worsening():
    # events crowd the end: [15, 16] in 16 h -> sum = ln(16/15) + 0 ~= 0.0645, beta = 2 / 0.0645 ~= 31 > 1
    result = metrics.growth([15.0, 16.0], 16.0)
    assert result.beta == pytest.approx(2.0 / math.log(16.0 / 15.0))
    assert result.improving is False


def test_growth_drops_events_outside_the_span():
    # 0 < t_i <= total_h: 0, -1 and 20 are dropped, leaving [2, 8] as in the hand example
    result = metrics.growth([0.0, -1.0, 2.0, 8.0, 20.0], 16.0)
    assert result.n == 2
    assert result.beta == pytest.approx(2.0 / math.log(16.0))
    assert result.mtbi_avg_h == pytest.approx(8.0)


def test_growth_is_none_with_fewer_than_two_events_or_a_zero_sum():
    assert metrics.growth([], 10.0) is None
    assert metrics.growth([5.0], 10.0) is None
    assert metrics.growth([5.0, 20.0], 10.0) is None  # one event left after dropping
    assert metrics.growth([10.0, 10.0], 10.0) is None  # every ln(T / t_i) is 0


def test_growth_input_order_does_not_matter():
    shuffled = metrics.growth([8.0, 2.0], 16.0)
    ordered = metrics.growth([2.0, 8.0], 16.0)
    assert shuffled.n == ordered.n
    assert shuffled.beta == pytest.approx(ordered.beta)
    assert shuffled.lam == pytest.approx(ordered.lam)


@pytest.mark.oracle
def test_growth_matches_the_reliability_package():
    """Oracle: the independent `reliability` package (Repairable_systems.reliability_growth, Crow-AMSAA).

    Its estimator is failure-terminated: it takes the end of observation T as the LAST event time
    (Beta = n / (n ln T - sum ln t_i) with T = max(times); Lambda = n / T ** Beta; DMTBF_I = 1 / (Lambda Beta T^(Beta-1))).
    The SPEC's fit is time-terminated (T = total_h, any time at or after the last event). The two coincide when
    total_h equals the last event time, so that is the case compared here.
    """
    pytest.importorskip("reliability")
    from reliability.Repairable_systems import reliability_growth

    event_h = [3.0, 10.0, 18.0, 40.0, 70.0, 100.0]
    oracle = reliability_growth(times=list(event_h), model="Crow-AMSAA", show_plot=False, print_results=False)
    result = metrics.growth(event_h, 100.0)
    assert result.n == len(event_h)
    assert result.beta == pytest.approx(float(oracle.Beta), rel=1e-9)
    assert result.lam == pytest.approx(float(oracle.Lambda), rel=1e-9)
    assert result.mtbi_now_h == pytest.approx(float(oracle.DMTBF_I), rel=1e-9)
    assert result.improving == (float(oracle.Beta) < 1.0)


def test_cumulative_hours():
    hours = {("arm-1", "2026-11-01"): 10.0, ("arm-1", "2026-11-02"): 4.0}
    episodes = [
        # 2026-11-02 06:00 UTC: all of 11-01 (10 h) + 4 h x 0.25 of the day elapsed = 11
        make_episode("later", t_of("2026-11-02", 6.0)),
        # 2026-11-01 12:00 UTC: no earlier day + 10 h x 0.5 = 5
        make_episode("earlier", t_of("2026-11-01", 12.0)),
    ]
    per_episode, total = metrics.cumulative_hours(episodes, hours, tz="UTC")
    assert per_episode == [pytest.approx(11.0), pytest.approx(5.0)]  # input order
    assert total == pytest.approx(14.0)


def test_cumulative_hours_robots_filter_and_days_without_hours():
    hours = {
        ("arm-1", "2026-11-01"): 10.0,
        ("arm-2", "2026-11-01"): 2.0,
        ("arm-1", "2026-11-03"): 8.0,
    }
    episodes = [make_episode("e", t_of("2026-11-03", 18.0))]
    # all robots: 12 (11-01) + 0 (11-02) + 8 x 0.75 = 18; total 20
    per_episode, total = metrics.cumulative_hours(episodes, hours, tz="UTC")
    assert per_episode == [pytest.approx(18.0)]
    assert total == pytest.approx(20.0)
    # arm-1 only: 10 + 0 + 6 = 16; total 18
    per_episode, total = metrics.cumulative_hours(episodes, hours, robots=["arm-1"], tz="UTC")
    assert per_episode == [pytest.approx(16.0)]
    assert total == pytest.approx(18.0)


def test_cumulative_hours_feed_growth():
    hours = {("arm-1", "2026-11-01"): 8.0, ("arm-1", "2026-11-02"): 8.0}
    episodes = [make_episode("a", t_of("2026-11-01", 6.0)), make_episode("b", t_of("2026-11-02", 0.0))]
    per_episode, total = metrics.cumulative_hours(episodes, hours, tz="UTC")
    # a: 8 x 0.25 = 2; b: 8 + 0 = 8; total 16 -> the hand example above
    assert per_episode == [pytest.approx(2.0), pytest.approx(8.0)]
    assert metrics.growth(per_episode, total).beta == pytest.approx(2.0 / math.log(16.0))
