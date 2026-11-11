"""Engine tests: causality, execution lag, and level construction.

The two properties worth protecting in a backtest are that no signal may see a
future bar, and that a signal is filled after it is produced. Both are easy to
break silently and impossible to spot by reading the equity curve.
"""

from __future__ import annotations

import random

from app import backtest, config, signals
from app.data import Candle


def synthetic(n: int = 420, seed: int = 11, drift: float = 0.25) -> list[Candle]:
    """Deterministic pseudo-random daily bars."""
    rng = random.Random(seed)
    price = 100.0
    bars: list[Candle] = []
    for i in range(n):
        change = rng.gauss(drift, 1.6)
        o = price
        c = max(1.0, o + change)
        h = max(o, c) + abs(rng.gauss(0, 0.6))
        l = min(o, c) - abs(rng.gauss(0, 0.6))
        bars.append(Candle(t=1_700_000_000 + i * 86400, o=o, h=h, l=l, c=c, v=1_000 + i))
        price = c
    return bars


def test_signal_at_a_bar_does_not_depend_on_later_bars():
    """Truncating the history must not change any earlier reading.

    If a signal, vote or gate at bar ``k`` differed when bars after ``k`` were
    removed, the engine would be peeking. This is the regression guard for that.
    """
    bars = synthetic()
    full = signals.series(bars)
    cut = 300
    truncated = signals.series(bars[:cut])

    assert len(full) == len(bars)
    assert len(truncated) == cut

    for i in (280, 290, cut - 1):
        a, b = full[i], truncated[i]
        assert a.score == b.score, f"score changed at bar {i} when future bars were removed"
        assert a.position == b.position, f"position changed at bar {i}"
        assert a.blocked == b.blocked
        assert [v.vote for v in a.votes] == [v.vote for v in b.votes]


def test_no_position_before_enough_history():
    bars = synthetic(120)
    points = signals.series(bars)
    for point in points:
        if point.block_reason == "insufficient history":
            assert point.position == 0
            assert point.bias == "neutral"


def test_backtest_enters_at_the_open_after_the_signal():
    bars = synthetic(seed=5)
    points = signals.series(bars)
    stats = backtest.run(bars, points, "stock")
    assert stats["trades"] > 0, "synthetic series produced no trades; test is blind"

    index_by_date = {}
    from datetime import datetime, timezone

    for i, bar in enumerate(bars):
        index_by_date[datetime.fromtimestamp(bar.t, tz=timezone.utc).strftime("%Y-%m-%d")] = i

    fee = config.COSTS_BPS["stock"] / 10_000.0
    checked = 0
    for trade in stats["trade_log"]:
        i = index_by_date[trade["entry_date"]]
        side = 1 if trade["side"] == "LONG" else -1
        expected = bars[i].o * (1 + side * fee)
        assert abs(trade["entry"] - expected) < 1e-6, f"entry {trade['entry']} != open fill {expected}"
        # The bar the signal came from must be earlier than the fill bar.
        assert i >= 1
        checked += 1
    assert checked > 0


def test_levels_put_the_stop_on_the_correct_side():
    long_levels = signals.levels(100.0, 2.0, 1)
    short_levels = signals.levels(100.0, 2.0, -1)

    assert long_levels["stop"] < 100.0 < short_levels["stop"]
    assert long_levels["risk_pct"] > 0
    assert "stop" in short_levels


def test_trailing_mode_does_not_invent_a_target():
    original = config.EXIT_MODE
    try:
        config.EXIT_MODE = "trailing"
        levels = signals.levels(100.0, 2.0, 1)
    finally:
        config.EXIT_MODE = original
    assert "target" not in levels
    assert levels["trail_atr"] == config.TRAIL_ATR


def test_fixed_mode_reports_a_target_and_r_multiple():
    original = config.EXIT_MODE
    try:
        config.EXIT_MODE = "fixed"
        fixed = signals.levels(100.0, 2.0, 1)
    finally:
        config.EXIT_MODE = original
    assert fixed["target"] > 100.0
    assert abs(fixed["rr"] - config.TARGET_ATR / config.STOP_ATR) < 1e-9


def test_partial_mode_takes_half_at_the_target():
    """A target hit must book half the position and keep the rest alive."""
    bars = synthetic(seed=5)
    points = signals.series(bars)
    stats = backtest.run(bars, points, "stock", exit_mode="partial")
    hits = [t for t in stats["trade_log"] if t["target_hit"]]
    assert hits, "no trade reached the target; test is blind"
    for trade in hits:
        assert "target (half)" in trade["reason"]
    # A round trip that hit its target and then trailed still counts as one trade.
    both = [t for t in hits if "+" in t["reason"]]
    assert both, "expected at least one round trip that scaled out then trailed"


def test_partial_mode_levels_expose_target_and_fraction():
    original = config.EXIT_MODE
    try:
        config.EXIT_MODE = "partial"
        levels = signals.levels(100.0, 2.0, 1)
    finally:
        config.EXIT_MODE = original
    assert levels["target"] > 100.0
    assert levels["partial"] == config.PARTIAL_FRACTION
    assert levels["rr"] > 0


def test_short_flips_to_long_when_the_signal_reverses():
    """A -1 -> +1 signal must close the short and open a long at the same open.

    Regression test: the executor used to only act on entries from flat and on
    exits to flat, so a decisive reversal was silently ignored and the old
    position stayed open until its stop.
    """
    n = 70
    bars = [Candle(t=1_700_000_000 + i * 86400, o=100.0, h=100.0, l=100.0, c=100.0, v=1_000.0) for i in range(n)]

    points = []
    for i, bar in enumerate(bars):
        if i < 20:
            side = 0
        elif i <= 40:
            side = -1
        else:
            side = 1
        points.append(
            signals.SignalPoint(
                i=i,
                t=bar.t,
                close=bar.c,
                score=2.5 * side,
                position=side,
                bias="short" if side < 0 else "long" if side > 0 else "neutral",
                atr=1.0,
                adx=25.0,
                rsi=50.0,
            )
        )

    stats = backtest.run(bars, points, "stock")
    trades = stats["trade_log"]
    assert len(trades) >= 2, trades

    short_leg = trades[0]
    long_leg = trades[1]
    assert short_leg["side"] == "SHORT"
    assert "flip" in short_leg["reason"], short_leg["reason"]
    assert long_leg["side"] == "LONG"

    # The flip signal lands on bar 41, so both fills belong to bar 42's open.
    from datetime import datetime, timezone

    flip_bar = datetime.fromtimestamp(bars[42].t, tz=timezone.utc).strftime("%Y-%m-%d")
    assert short_leg["exit_date"] == flip_bar
    assert long_leg["entry_date"] == flip_bar
    fee = config.COSTS_BPS["stock"] / 10_000.0
    expected_fill = 100.0 * (1 + fee)  # closing a short and opening a long both pay up
    assert abs(short_leg["exit"] - expected_fill) < 1e-9
    assert abs(long_leg["entry"] - expected_fill) < 1e-9
    assert stats["live"]["side"] == 1  # still long at the final bar


def test_costs_reduce_returns_versus_a_costless_run():
    bars = synthetic(seed=9)
    points = signals.series(bars)
    original = dict(config.COSTS_BPS)
    try:
        config.COSTS_BPS["stock"] = 0.0
        free = backtest.run(bars, points, "stock")["total_return_pct"]
        config.COSTS_BPS["stock"] = 60.0
        expensive = backtest.run(bars, points, "stock")["total_return_pct"]
    finally:
        config.COSTS_BPS.update(original)
    assert expensive < free


def test_long_only_mode_never_opens_a_short():
    """Shorts are off by default on the evidence; the stance must stay >= 0."""
    original = config.ALLOW_SHORTS
    try:
        config.ALLOW_SHORTS = False
        points = signals.series(synthetic(seed=13))
        assert points
        assert all(point.position >= 0 for point in points)
        assert any(point.position > 0 for point in points), "long-only still has to trade"
        # The bearish score is still computed and reported, just not acted on.
        assert any(point.score < -config.ENTRY_SCORE for point in points)
    finally:
        config.ALLOW_SHORTS = original


def test_shorts_come_back_when_enabled():
    original = config.ALLOW_SHORTS
    try:
        config.ALLOW_SHORTS = True
        points = signals.series(synthetic(seed=13))
        assert any(point.position < 0 for point in points)
    finally:
        config.ALLOW_SHORTS = original
