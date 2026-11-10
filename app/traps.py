"""Liquidation-trap detection on daily bars.

A trap is the footprint a forced unwind leaves behind: price is pushed through a
level where stops and leverage sit, cannot hold there, and closes back inside the
prior range. Longs get liquidated below support (bullish trap, squeeze up);
shorts get liquidated above resistance (bearish trap, squeeze down).

What is measured, and what is inferred:

* **Stocks** — the price/volume footprint only. There is no public liquidation
  tape, so this is a proxy: a sweep of a prior extreme, a close back inside the
  range, and volume above its own average.
* **Crypto** — the same price footprint, plus the actual leverage data from the
  futures market: open-interest change on the day and the funding rate. An
  open-interest drop on the sweep bar is direct evidence that positions were
  force-closed rather than merely offered.

Nothing here is fitted: the thresholds are conventional and fixed up front, and
scripts/research_traps.py measures the result against an unconditional baseline.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional, Sequence

from . import config, indicators
from .data import Candle


@dataclass
class TrapEvent:
    i: int
    t: int
    side: int  # +1 = bullish trap (swept lows, squeeze up); -1 = bearish
    level: float  # the prior extreme that was swept
    close: float
    atr: float
    penetration_atr: float  # how far beyond the level, in ATR units
    volume_ratio: float
    body_close_pos: float  # 0 = low of bar, 1 = high of bar

    def as_dict(self) -> dict:
        d = asdict(self)
        d["kind"] = "bullish" if self.side > 0 else "bearish"
        return d


def detect(
    candles: Sequence[Candle],
    lookback: int = 20,
    min_penetration: float = 0.15,
    max_penetration: float = 1.2,
    volume_mult: float = 1.2,
    use_volume: bool = True,
    cooldown: int = 5,
) -> list[TrapEvent]:
    """Find trap bars.

    ``min_penetration`` rejects one-tick pokes; ``max_penetration`` rejects real
    breakdowns — a move that far beyond the level is a trend, not a failed raid.
    ``cooldown`` keeps clustered bars from counting as independent events.
    """
    n = len(candles)
    if n < lookback + 30:
        return []

    highs = [c.h for c in candles]
    lows = [c.l for c in candles]
    closes = [c.c for c in candles]
    volumes = [c.v for c in candles]

    atr_series = indicators.atr(highs, lows, closes, config.ATR_PERIOD)
    vol_series = indicators.sma(volumes, 20)

    events: list[TrapEvent] = []
    blocked_until = -1

    for i in range(max(lookback, config.ATR_PERIOD) + 1, n):
        atr = atr_series[i]
        vol_avg = vol_series[i]
        if not atr or not vol_avg or i <= blocked_until:
            continue

        prior_low = min(lows[i - lookback : i])
        prior_high = max(highs[i - lookback : i])
        bar_range = highs[i] - lows[i]
        if bar_range <= 0:
            continue

        close_pos = (closes[i] - lows[i]) / bar_range
        vol_ratio = volumes[i] / vol_avg if vol_avg else 0.0

        side = 0
        level = 0.0
        penetration = 0.0

        # Bullish trap: swept the prior low, closed back above it, upper half.
        if lows[i] < prior_low and closes[i] > prior_low and close_pos >= 0.5:
            pen = (prior_low - lows[i]) / atr
            if min_penetration <= pen <= max_penetration:
                side, level, penetration = 1, prior_low, pen

        # Bearish trap: swept the prior high, closed back below it, lower half.
        elif highs[i] > prior_high and closes[i] < prior_high and close_pos <= 0.5:
            pen = (highs[i] - prior_high) / atr
            if min_penetration <= pen <= max_penetration:
                side, level, penetration = -1, prior_high, pen

        if side == 0:
            continue
        if use_volume and vol_ratio < volume_mult:
            continue

        events.append(
            TrapEvent(
                i=i,
                t=candles[i].t,
                side=side,
                level=level,
                close=closes[i],
                atr=atr,
                penetration_atr=round(penetration, 3),
                volume_ratio=round(vol_ratio, 2),
                body_close_pos=round(close_pos, 3),
            )
        )
        blocked_until = i + cooldown

    return events


def forward_return(candles: Sequence[Candle], i: int, side: int, horizon: int) -> Optional[float]:
    """Sign-adjusted return from the next open to the close ``horizon`` bars later.

    Entering on the next open is the earliest a signal formed at bar ``i``'s
    close can be acted on, so it is the only fill that is not look-ahead.
    """
    n = len(candles)
    if i + 1 >= n:
        return None
    entry = candles[i + 1].o
    j = min(i + horizon, n - 1)
    if j <= i or entry <= 0:
        return None
    return side * (candles[j].c / entry - 1) * 100


def study(
    candles: Sequence[Candle],
    events: Sequence[TrapEvent],
    horizons: Sequence[int] = (5, 10, 20),
) -> dict[int, list[float]]:
    """Forward returns per horizon, in percent, sign-adjusted for the trap direction."""
    out: dict[int, list[float]] = {h: [] for h in horizons}
    for event in events:
        for h in horizons:
            value = forward_return(candles, event.i, event.side, h)
            if value is not None:
                out[h].append(value)
    return out


def baseline(
    candles: Sequence[Candle],
    horizons: Sequence[int] = (5, 10, 20),
    step: int = 1,
) -> dict[int, list[float]]:
    """Unconditional forward returns, same fill convention, for comparison."""
    out: dict[int, list[float]] = {h: [] for h in horizons}
    for i in range(0, len(candles) - max(horizons) - 1, max(step, 1)):
        for h in horizons:
            value = forward_return(candles, i, 1, h)
            if value is not None:
                out[h].append(value)
    return out
