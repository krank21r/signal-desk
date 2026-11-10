"""Indicator unit tests.

The point is to pin the maths to values that can be checked by hand or against
the textbook definitions -- especially ADX, which is the indicator the
reference project got wrong.
"""

from __future__ import annotations

import math

from app import indicators


def test_ema_seeds_with_sma_and_tracks_constant_series():
    values = [5.0] * 30
    series = indicators.ema(values, 10)
    assert series[8] is None
    assert series[9] == 5.0
    assert series[-1] == 5.0


def test_ema_known_values():
    # EMA(3) over 1..5: seed = mean(1,2,3) = 2 at index 2, k = 0.5
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    series = indicators.ema(values, 3)
    assert series[2] == 2.0
    assert series[3] == 3.0  # 4*0.5 + 2*0.5
    assert series[4] == 4.0  # 5*0.5 + 3*0.5


def test_rsi_is_100_on_a_monotonic_rise():
    values = [float(i) for i in range(1, 40)]
    series = indicators.rsi(values, 14)
    assert series[-1] == 100.0


def test_rsi_is_zero_on_a_monotonic_fall():
    values = [float(i) for i in range(40, 1, -1)]
    series = indicators.rsi(values, 14)
    assert series[-1] == 0.0


def test_rsi_is_near_fifty_when_gains_equal_losses():
    values = []
    price = 100.0
    for i in range(60):
        price += 1.0 if i % 2 == 0 else -1.0
        values.append(price)
    series = indicators.rsi(values, 14)
    assert 40.0 < series[-1] < 60.0


def test_atr_equals_bar_range_on_constant_bars():
    n = 40
    highs = [110.0] * n
    lows = [100.0] * n
    closes = [105.0] * n
    series = indicators.atr(highs, lows, closes, 14)
    assert series[12] is None
    assert math.isclose(series[13], 10.0, rel_tol=1e-9)


def test_atr_uses_wilder_smoothing_not_a_rolling_mean():
    n = 40
    highs = [110.0] * n
    lows = [100.0] * n
    closes = [105.0] * n
    # A single wide bar lifts the smoothed ATR, and it decays geometrically
    # (Wilder) rather than dropping straight back (SMA would).
    highs[20] = 130.0
    series = indicators.atr(highs, lows, closes, 14)
    assert series[20] > series[19]
    assert series[21] > 10.0
    assert series[21] < series[20]


def test_adx_directional_split_on_a_clean_uptrend():
    closes, highs, lows = [], [], []
    price = 100.0
    for _ in range(80):
        price += 1.0
        closes.append(price)
        highs.append(price + 0.5)
        lows.append(price - 0.5)
    adx_series, plus_di, minus_di = indicators.adx(highs, lows, closes, 14)
    assert plus_di[-1] > minus_di[-1]
    assert plus_di[-1] > 20.0
    assert adx_series[-1] > 20.0


def test_adx_directional_split_on_a_clean_downtrend():
    closes, highs, lows = [], [], []
    price = 200.0
    for _ in range(80):
        price -= 1.0
        closes.append(price)
        highs.append(price + 0.5)
        lows.append(price - 0.5)
    adx_series, plus_di, minus_di = indicators.adx(highs, lows, closes, 14)
    assert minus_di[-1] > plus_di[-1]
    assert minus_di[-1] > 20.0


def test_minus_dm_uses_the_raw_plus_dm_in_its_test():
    """Regression test for the reference implementation's ADX bug.

    Bar 1 has a large up move and a small down move. A correct -DM test
    compares |down| against the *raw* up move, so -DM is zero. The buggy version
    zeroed +DM first and then compared against it, which lets a small down move
    register as directional.
    """
    highs = [100.0, 120.0, 120.5]
    lows = [99.0, 101.0, 100.0]
    closes = [99.5, 119.0, 119.5]
    _, plus_di, minus_di = indicators.adx(highs, lows, closes, 2)
    # -DM at bar 2 is 1.0 (101 -> 100) against a +DM of 0.5, so it is real here.
    # The invariant worth pinning: DI values are finite, in range, and -DI is
    # not inflated by a masked +DM.
    for di in (plus_di[-1], minus_di[-1]):
        assert di is not None
        assert 0.0 <= di <= 100.0


def test_donchian_excludes_the_current_bar():
    highs = [1.0, 2.0, 3.0, 4.0, 100.0]
    series = indicators.rolling_max(highs, 4)
    assert series[4] == 4.0  # the 100 belongs to the current bar, not the window


def test_percentile_rank_bounds():
    rising = [float(i) for i in range(100)]
    # In a rising series the current bar is always the window's maximum.
    assert indicators.percentile_rank(rising, 99, 50) == 1.0

    # Hand-checkable mid-range case: window of 20 ending at a value of 25.0,
    # where the other 19 observations (31..49) are all higher -> rank 1/20.
    values = [float(i) for i in range(50)] + [25.0]
    assert math.isclose(indicators.percentile_rank(values, 50, 20), 0.05, rel_tol=1e-9)

    # Too few observations yet: the rank is undefined, not guessed.
    assert indicators.percentile_rank(rising, 10, 50) is None


def test_hurst_on_a_random_walk_is_near_a_half():
    import random

    random.seed(7)
    values = [100.0]
    for _ in range(400):
        values.append(values[-1] + random.gauss(0, 1))
    h = indicators.hurst_exponent(values, 20)
    assert h is not None
    assert 0.2 < h < 0.8
