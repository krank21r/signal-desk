"""Detector tests: the sweep/reclaim footprint must be read exactly as defined."""

from __future__ import annotations

from app import traps
from app.data import Candle


def build(pierce_low: float, close: float, volume: float = 2000.0, n: int = 60) -> list[Candle]:
    """Flat 100 +/- 1 bars, then one bar that pierces ``pierce_low`` and closes at ``close``."""
    bars = [Candle(t=1_700_000_000 + i * 86400, o=100.0, h=101.0, l=99.0, c=100.0, v=1000.0) for i in range(n)]
    i = 50
    bars[i] = Candle(t=bars[i].t, o=100.0, h=max(close, 100.0), l=pierce_low, c=close, v=volume)
    return bars


def test_bullish_sweep_that_reclaims_is_detected():
    events = traps.detect(build(pierce_low=97.0, close=100.5))
    assert len(events) == 1
    event = events[0]
    assert event.side == 1
    assert event.level == 99.0  # the prior 20-bar low
    assert 0.15 <= event.penetration_atr <= 1.2


def test_breakdown_that_closes_below_the_level_is_not_a_trap():
    events = traps.detect(build(pierce_low=97.0, close=97.5))
    assert events == []


def test_pierce_that_is_too_deep_is_a_breakdown_not_a_trap():
    # 5 ATR through the level: that is a real break, not a raid on stops.
    events = traps.detect(build(pierce_low=91.0, close=100.5))
    assert events == []


def test_volume_confirmation_can_be_required():
    quiet = build(pierce_low=97.0, close=100.5, volume=900.0)
    assert traps.detect(quiet, use_volume=True) == []
    assert len(traps.detect(quiet, use_volume=False)) == 1


def test_bearish_mirror():
    bars = build(pierce_low=99.0, close=100.0)
    i = 50
    # Sweep above the prior high (101) and close back below it, in the lower half.
    bars[i] = Candle(t=bars[i].t, o=100.0, h=103.0, l=99.5, c=100.0, v=2000.0)
    events = traps.detect(bars)
    assert len(events) == 1
    assert events[0].side == -1
    assert events[0].level == 101.0


def test_forward_return_uses_the_next_open():
    bars = build(pierce_low=97.0, close=100.5)
    i = 50
    bars[i + 1] = Candle(t=bars[i + 1].t, o=100.0, h=106.0, l=100.0, c=105.0, v=1000.0)
    value = traps.forward_return(bars, i, side=1, horizon=1)
    assert value is not None
    assert abs(value - 5.0) < 1e-9  # 100.0 -> 105.0, in percent
