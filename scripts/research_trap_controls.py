"""Control tests for the trap effect.

Two questions the headline numbers cannot answer on their own:

1. **Placebo** — do bars that look similar (heavy volume, down bar, close off the
   low) but did *not* sweep a prior extreme behave the same? If yes, the "trap"
   is not doing the work; volatility and mean reversion are.
2. **Regime** — does the crypto result survive year by year, or does it come from
   a handful of cascade days in one regime?

    python scripts/research_traps.py --controls
"""

from __future__ import annotations

import math
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import config, data, indicators, traps  # noqa: E402
from app.traps import TrapEvent  # noqa: E402

HORIZONS = (5, 10, 20)


def day(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def year(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y")


def summarise(values):
    if len(values) < 3:
        return None
    mean = sum(values) / len(values)
    sd = statistics.stdev(values)
    return {
        "n": len(values),
        "hit": sum(1 for v in values if v > 0) / len(values) * 100,
        "mean": mean,
        "t": mean / (sd / math.sqrt(len(values))) if sd else None,
    }


def placebo_events(candles, lookback=20, volume_mult=1.2, cooldown=5):
    """Heavy-volume reversal bars that did NOT break the prior extreme."""
    highs = [c.h for c in candles]
    lows = [c.l for c in candles]
    closes = [c.c for c in candles]
    vols = [c.v for c in candles]
    vol_sma = indicators.sma(vols, 20)
    atr = indicators.atr(highs, lows, closes, config.ATR_PERIOD)

    out = []
    blocked = -1
    for i in range(lookback + 1, len(candles)):
        if not vol_sma[i] or not atr[i] or i <= blocked:
            continue
        rng = highs[i] - lows[i]
        if rng <= 0 or vols[i] / vol_sma[i] < volume_mult:
            continue
        close_pos = (closes[i] - lows[i]) / rng
        prior_low = min(lows[i - lookback : i])
        prior_high = max(highs[i - lookback : i])
        side = 0
        if closes[i] < candles[i - 1].c and close_pos >= 0.5 and lows[i] >= prior_low:
            side = 1  # down bar, closed strong, but never took out the low
        elif closes[i] > candles[i - 1].c and close_pos <= 0.5 and highs[i] <= prior_high:
            side = -1
        if side:
            out.append(
                TrapEvent(
                    i=i,
                    t=candles[i].t,
                    side=side,
                    level=prior_low if side > 0 else prior_high,
                    close=closes[i],
                    atr=atr[i],
                    penetration_atr=0.0,
                    volume_ratio=round(vols[i] / vol_sma[i], 2),
                    body_close_pos=round(close_pos, 3),
                )
            )
            blocked = i + cooldown
    return out


def excess_for(universe, kind, detector, drift, lookback=20):
    per_h = {h: [] for h in HORIZONS}
    for symbol, k, candles, _ in universe:
        if k != kind:
            continue
        events = detector(candles, lookback)
        rets = traps.study(candles, events, HORIZONS)
        for h in HORIZONS:
            for event, value in zip(events, rets[h]):
                per_h[h].append(value - event.side * drift[(kind, h)])
    return per_h


def main() -> int:
    universe = []
    for symbol in config.all_symbols():
        try:
            candles = data.get_candles(symbol)
        except data.DataError as exc:
            print(f"skip {symbol}: {exc}")
            continue
        closes = [c.c for c in candles]
        universe.append((symbol, config.asset_class(symbol), candles, indicators.ema(closes, 200)))

    drift = {}
    for kind in ("stock", "crypto"):
        rows = {h: [] for h in HORIZONS}
        for _, k, candles, _ in universe:
            if k != kind:
                continue
            base = traps.baseline(candles, HORIZONS, step=3)
            for h in HORIZONS:
                rows[h].extend(base[h])
        for h in HORIZONS:
            drift[(kind, h)] = sum(rows[h]) / len(rows[h]) if rows[h] else 0.0

    trap_detector = lambda c, lb: traps.detect(c, lookback=lb, volume_mult=1.2)  # noqa: E731
    placebo_detector = lambda c, lb: placebo_events(c, lookback=lb, volume_mult=1.2)  # noqa: E731

    print("=" * 92)
    print("trap vs placebo  (excess return, same direction, same horizon)")
    print("=" * 92)
    print(f"{'class':<8}{'level':<7}{'sample':<10}{'h':>3}{'n':>6}{'hit%':>8}{'excess%':>10}{'t':>7}")
    for kind in ("crypto", "stock"):
        for lookback in (20, 55):
            for name, detector in (("trap", trap_detector), ("placebo", placebo_detector)):
                data_h = excess_for(universe, kind, detector, drift, lookback)
                for h in HORIZONS:
                    row = summarise(data_h[h])
                    if not row:
                        continue
                    print(f"{kind:<8}{lookback:<7}{name:<10}{h:>3}{row['n']:>6}{row['hit']:>8.1f}"
                          f"{row['mean']:>10.2f}{(row['t'] if row['t'] is not None else float('nan')):>7.2f}")

    print("\n" + "=" * 92)
    print("crypto trap, 5-day excess by year (is it one regime?)")
    print("=" * 92)
    for lookback in (20, 55):
        buckets = {}
        for symbol, kind, candles, _ in universe:
            if kind != "crypto":
                continue
            events = trap_detector(candles, lookback)
            rets = traps.study(candles, events, (5,))
            for event, value in zip(events, rets[5]):
                buckets.setdefault(year(event.t), []).append(value - event.side * drift[("crypto", 5)])
        for year_key in sorted(buckets):
            row = summarise(buckets[year_key])
            if row:
                print(f"lookback {lookback:<4}{year_key:<6}n={row['n']:<5}hit={row['hit']:>5.1f}%  "
                      f"excess={row['mean']:+.2f}%  t={row['t']:+.2f}" if row["t"] is not None else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
