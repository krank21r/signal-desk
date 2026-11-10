"""Event study: do liquidation traps actually pay?

Four questions, each measured against an unconditional baseline:

1. Does a sweep of a prior extreme that closes back inside the range revert?
2. Does volume confirmation improve it?
3. Does it only work when the sweep is *with* the 200-day trend (buy the dip in
   an uptrend, sell the rally in a downtrend)?
4. Do deeper levels (55-bar rather than 20-bar) matter more?

Excess return subtracts the drift you would have earned simply holding the same
direction for the same horizon, so "excess" is the part attributable to the trap.

    python scripts/research_traps.py
"""

from __future__ import annotations

import math
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import config, data, indicators, traps  # noqa: E402

HORIZONS = (5, 10, 20)
LOOKBACKS = (20, 55)
TRENDS = (False, True)


def day(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def summarise(values: Sequence[float]) -> dict | None:
    if len(values) < 3:
        return None
    mean = sum(values) / len(values)
    sd = statistics.stdev(values)
    t = mean / (sd / math.sqrt(len(values))) if sd else None
    return {
        "n": len(values),
        "hit": sum(1 for v in values if v > 0) / len(values) * 100,
        "mean": mean,
        "t": t,
    }


def trend_ok(candles, ema200, event) -> bool:
    """Bullish traps need price above the 200-day EMA, bearish below it."""
    value = ema200[event.i]
    if value is None:
        return False
    return (event.side > 0 and candles[event.i].c > value) or (event.side < 0 and candles[event.i].c < value)


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

    drift: dict[tuple[str, int], float] = {}
    for kind in ("stock", "crypto"):
        pooled = [c for _, k, c, _ in universe if k == kind]
        rows = {h: [] for h in HORIZONS}
        for candles in pooled:
            base = traps.baseline(candles, HORIZONS, step=3)
            for h in HORIZONS:
                rows[h].extend(base[h])
        for h in HORIZONS:
            drift[(kind, h)] = sum(rows[h]) / len(rows[h]) if rows[h] else 0.0
        print(f"{kind}: unconditional drift over {HORIZONS} bars = "
              + ", ".join(f"{drift[(kind, h)]:+.2f}%" for h in HORIZONS))

    print("\n" + "=" * 104)
    print(f"{'class':<8}{'level':<7}{'trend':<7}{'h':>3}{'n':>6}{'hit%':>8}{'mean%':>9}{'excess%':>10}{'t':>7}{'':>3}")
    print("=" * 104)

    best_rows = []
    for kind in ("stock", "crypto"):
        for lookback in LOOKBACKS:
            for use_trend in TRENDS:
                per_h = {h: [] for h in HORIZONS}
                excess = {h: [] for h in HORIZONS}
                for symbol, k, candles, ema200 in universe:
                    if k != kind:
                        continue
                    events = traps.detect(candles, lookback=lookback, volume_mult=1.2)
                    if use_trend:
                        events = [e for e in events if trend_ok(candles, ema200, e)]
                    rets = traps.study(candles, events, HORIZONS)
                    for h in HORIZONS:
                        for event, value in zip(events, rets[h]):
                            per_h[h].append(value)
                            excess[h].append(value - event.side * drift[(kind, h)])

                for h in HORIZONS:
                    raw = summarise(per_h[h])
                    rel = summarise(excess[h])
                    if not raw or not rel:
                        continue
                    flag = "*" if rel["t"] is not None and abs(rel["t"]) >= 2 and len(per_h[h]) >= 40 else ""
                    print(
                        f"{kind:<8}{lookback:<7}{'yes' if use_trend else 'no':<7}{h:>3}{rel['n']:>6}"
                        f"{raw['hit']:>8.1f}{raw['mean']:>9.2f}{rel['mean']:>10.2f}"
                        f"{(rel['t'] if rel['t'] is not None else float('nan')):>7.2f}{flag:>3}"
                    )
                    best_rows.append((kind, lookback, use_trend, h, rel, raw))

    print("\n" + "=" * 104)
    print("* = |t| >= 2 with at least 40 events. Treat the whole matrix as exploratory:"
          "\ntesting 24 cells means one or two will look good by chance.")

    # ------------------------------------------------------------------
    # what the futures tape said on the event day
    # ------------------------------------------------------------------
    print("\n" + "=" * 104)
    print("crypto only: was leverage actually being flushed?")
    print("=" * 104)
    oi_counts = []
    for symbol in config.BINANCE_PAIRS:
        series = data.get_open_interest(symbol)
        if series:
            oi_counts.append((symbol, len(series), day(series[0]["t"]), day(series[-1]["t"])))
    if oi_counts:
        counts = [c[1] for c in oi_counts]
        print(f"open-interest history available: {min(counts)}-{max(counts)} daily rows per symbol "
              f"({oi_counts[0][2]} -> {oi_counts[-1][3]} for {oi_counts[0][0]})")
        print("conclusion: Binance's public endpoint serves roughly the last 30 days only, so an")
        print("open-interest-flush test cannot be run over a multi-year sample. It can only be")
        print("logged forward, or bought from a vendor with deeper history.")
    funding = data.get_funding_rates("BTC-USD")
    if funding:
        print(f"funding history: {len(funding)} rows ({day(funding[0]['t'])} -> {day(funding[-1]['t'])})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
