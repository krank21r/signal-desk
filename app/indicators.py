"""Indicator maths, implemented in plain Python.

Every function is causal: the value at index ``i`` uses only data up to ``i``.
Series are returned aligned to the input list, with ``None`` during warmup.

Wilder's smoothing is used for RSI, ATR and ADX, which is what the original
formulas (and every charting platform) use. The commonly mis-transcribed
variants -- an SMA of true range, or applying a rolling mean to DX -- are not
used here.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

Series = list[Optional[float]]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _wilder(values: Sequence[Optional[float]], period: int) -> Series:
    """Wilder's recursive average: avg = (prev * (n - 1) + x) / n.

    Seeded with the mean of the first ``period`` valid values, which is how
    Wilder defined the initial ATR/RSI reading. Leading ``None`` values are
    skipped so this also works on series that start late (e.g. DX).
    """
    out: Series = [None] * len(values)
    start = next((i for i, v in enumerate(values) if v is not None), None)
    if start is None:
        return out
    end = start + period
    if end > len(values) or any(v is None for v in values[start:end]):
        return out
    total = 0.0
    for v in values[start:end]:
        assert v is not None
        total += v
    prev = total / period
    out[end - 1] = prev
    for i in range(end, len(values)):
        v = values[i]
        if v is None:
            out[i] = out[i - 1]
            continue
        prev = (prev * (period - 1) + v) / period
        out[i] = prev
    return out


def last_valid(series: Sequence[Optional[float]]) -> Optional[float]:
    for v in reversed(series):
        if v is not None:
            return v
    return None


def sma(values: Sequence[float], period: int) -> Series:
    out: Series = [None] * len(values)
    if len(values) < period:
        return out
    running = sum(values[:period])
    out[period - 1] = running / period
    for i in range(period, len(values)):
        running += values[i] - values[i - period]
        out[i] = running / period
    return out


def ema(values: Sequence[float], period: int) -> Series:
    out: Series = [None] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def stdev(values: Sequence[float], period: int) -> Series:
    """Sample standard deviation over a rolling window."""
    out: Series = [None] * len(values)
    if len(values) < period:
        return out
    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        mean = sum(window) / period
        var = sum((v - mean) ** 2 for v in window) / (period - 1)
        out[i] = var ** 0.5
    return out


def roc(values: Sequence[float], period: int) -> Series:
    """Rate of change in percent."""
    out: Series = [None] * len(values)
    for i in range(period, len(values)):
        base = values[i - period]
        if base:
            out[i] = (values[i] / base - 1.0) * 100.0
    return out


def rolling_max(values: Sequence[float], period: int, exclude_current: bool = True) -> Series:
    """Highest value over ``period`` bars. By default the current bar is excluded,
    so a breakout test compares today's close against prior structure."""
    out: Series = [None] * len(values)
    start = period if exclude_current else period - 1
    for i in range(start, len(values)):
        end = i if exclude_current else i + 1
        out[i] = max(values[end - period : end])
    return out


def rolling_min(values: Sequence[float], period: int, exclude_current: bool = True) -> Series:
    out: Series = [None] * len(values)
    start = period if exclude_current else period - 1
    for i in range(start, len(values)):
        end = i if exclude_current else i + 1
        out[i] = min(values[end - period : end])
    return out


def percentile_rank(values: Sequence[Optional[float]], index: int, lookback: int) -> Optional[float]:
    """Fraction of the trailing window that is <= ``values[index]`` (0..1)."""
    if index < 1:
        return None
    lo = max(0, index - lookback + 1)
    window = [v for v in values[lo : index + 1] if v is not None]
    if len(window) < max(20, lookback // 4):
        return None
    current = values[index]
    if current is None:
        return None
    return sum(1 for v in window if v <= current) / len(window)


# --------------------------------------------------------------------------
# true range / ATR
# --------------------------------------------------------------------------


def true_range(high: Sequence[float], low: Sequence[float], close: Sequence[float]) -> list[float]:
    out = [high[0] - low[0]]
    for i in range(1, len(close)):
        out.append(max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1])))
    return out


def atr(high: Sequence[float], low: Sequence[float], close: Sequence[float], period: int = 14) -> Series:
    return _wilder(true_range(high, low, close), period)


# --------------------------------------------------------------------------
# RSI
# --------------------------------------------------------------------------


def rsi(close: Sequence[float], period: int = 14) -> Series:
    gains: Series = [None]
    losses: Series = [None]
    for i in range(1, len(close)):
        delta = close[i] - close[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = _wilder(gains, period)
    avg_loss = _wilder(losses, period)
    out: Series = [None] * len(close)
    for i in range(len(close)):
        g, l = avg_gain[i], avg_loss[i]
        if g is None or l is None:
            continue
        if l == 0:
            out[i] = 100.0
        else:
            rs = g / l
            out[i] = 100.0 - (100.0 / (1.0 + rs))
    return out


# --------------------------------------------------------------------------
# ADX / directional movement
# --------------------------------------------------------------------------


def adx(
    high: Sequence[float],
    low: Sequence[float],
    close: Sequence[float],
    period: int = 14,
) -> tuple[Series, Series, Series]:
    """Returns (adx, plus_di, minus_di).

    Directional movement is measured against the *raw* prior bar, and the
    +DM / -DM test is symmetric: +DM requires an up move larger than the down
    move, -DM requires a down move larger than the up move. Masking one before
    testing the other corrupts -DI, which is the bug in the reference
    implementation this project started from.
    """
    n = len(close)
    plus_dm: list[float] = [0.0] * n
    minus_dm: list[float] = [0.0] * n
    for i in range(1, n):
        up = high[i] - high[i - 1]
        down = low[i - 1] - low[i]
        plus_dm[i] = up if (up > 0 and up > down) else 0.0
        minus_dm[i] = down if (down > 0 and down > up) else 0.0

    tr = true_range(high, low, close)
    smooth_tr = _wilder(tr, period)
    smooth_plus = _wilder(plus_dm, period)
    smooth_minus = _wilder(minus_dm, period)

    plus_di: Series = [None] * n
    minus_di: Series = [None] * n
    for i in range(n):
        st, sp, sm = smooth_tr[i], smooth_plus[i], smooth_minus[i]
        if st is None or sp is None or sm is None or st == 0:
            continue
        plus_di[i] = 100.0 * sp / st
        minus_di[i] = 100.0 * sm / st

    dx: Series = [None] * n
    for i in range(n):
        p, m = plus_di[i], minus_di[i]
        if p is None or m is None:
            continue
        denom = p + m
        dx[i] = 0.0 if denom == 0 else 100.0 * abs(p - m) / denom

    return _wilder(dx, period), plus_di, minus_di


# --------------------------------------------------------------------------
# Hurst exponent (kept from the reference project's stat-arb family)
# --------------------------------------------------------------------------


def hurst_exponent(values: Sequence[float], max_lag: int = 20) -> Optional[float]:
    """Rescaled-range style estimate. ~0.5 random walk, >0.5 trending, <0.5 mean reverting."""
    if len(values) < max_lag * 2:
        return None
    lags: list[int] = []
    taus: list[float] = []
    for lag in range(2, max_lag + 1):
        diffs = [values[i + lag] - values[i] for i in range(len(values) - lag)]
        if not diffs:
            continue
        mean = sum(diffs) / len(diffs)
        var = sum((d - mean) ** 2 for d in diffs) / len(diffs)
        std = var ** 0.5
        if std <= 0:
            continue
        lags.append(lag)
        taus.append(std)
    if len(lags) < 4:
        return None
    # least squares on log(lag) vs log(tau)
    xs = [math.log(lag) for lag in lags]
    ys = [math.log(tau) for tau in taus]
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return None
    return num / den
