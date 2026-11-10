"""Falsification test: is "fade the sweep" in stocks a real signal?

Pre-committed criteria (fixed before looking at either side):

  1. Same sign and |t| >= 2 in BOTH halves, on date-clustered averages so ten
     symbols moving on the same day count once.
  2. Median and 10%-trimmed mean agree with the mean -- one cascade day must not
     carry it.
  3. Beats the placebo (heavy-volume weak closes that never swept the extreme)
     by at least 0.5pp.
  4. Still positive after 2bps per side.

Both sides are printed. THE BULLISH SIDE IS THE PRE-REGISTERED TEST. The bearish
side was looked at second, after seeing its t-stat, so its evidential value is
lower -- it is one cell of a 25-cell search and would need forward validation
even if it clears every bar.

    python scripts/test_sweep_short.py
"""

from __future__ import annotations

import math
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import config, data, traps  # noqa: E402
from scripts.research_trap_controls import placebo_events  # noqa: E402

HORIZON = 5
COST_PCT = 2 * config.COSTS_BPS["stock"] / 100.0  # round trip, in percent of notional


def day(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def tstat(values: list[float]) -> float | None:
    if len(values) < 3:
        return None
    sd = statistics.stdev(values)
    if not sd:
        return None
    return (sum(values) / len(values)) / (sd / math.sqrt(len(values)))


def trimmed(values: list[float], cut: float = 0.1) -> float:
    if len(values) < 10:
        return statistics.mean(values)
    ordered = sorted(values)
    k = int(len(ordered) * cut)
    return statistics.mean(ordered[k : len(ordered) - k])


def welch(a: list[float], b: list[float]) -> float | None:
    if len(a) < 3 or len(b) < 3:
        return None
    va = statistics.variance(a) / len(a)
    vb = statistics.variance(b) / len(b)
    if not (va + vb):
        return None
    return (statistics.mean(a) - statistics.mean(b)) / math.sqrt(va + vb)


def collect(events_by_symbol):
    """Returns the fade-trade returns (opposite of the sweep direction), net of costs."""
    pooled: list[float] = []
    by_date: dict[str, list[float]] = defaultdict(list)
    for candles, events in events_by_symbol:
        for event in events:
            swept_ret = traps.forward_return(candles, event.i, event.side, HORIZON)
            if swept_ret is None:
                continue
            fade = -swept_ret - COST_PCT
            pooled.append(fade)
            by_date[day(event.t)].append(fade)
    return pooled, by_date


def evaluate(title: str, events_by_symbol, placebo_by_symbol) -> list[bool]:
    pooled, by_date = collect(events_by_symbol)
    if len(pooled) < 10:
        print(f"\n{title}: only {len(pooled)} events -- not testable.\n")
        return [False]

    clusters = [statistics.mean(v) for v in by_date.values()]
    dates = sorted(by_date)
    midpoint = dates[len(dates) // 2]
    first = [statistics.mean(v) for d, v in sorted(by_date.items()) if d < midpoint]
    second = [statistics.mean(v) for d, v in sorted(by_date.items()) if d >= midpoint]
    placebo_pooled, _ = collect(placebo_by_symbol)

    years: dict[str, list[float]] = defaultdict(list)
    for candles, events in events_by_symbol:
        for event in events:
            swept_ret = traps.forward_return(candles, event.i, event.side, HORIZON)
            if swept_ret is not None:
                years[day(event.t)[:4]].append(-swept_ret - COST_PCT)

    print(f"\n{title}: {len(pooled)} events over {len(dates)} days ({dates[0]} -> {dates[-1]})")
    print(f"{'measure':<32}{'n':>6}{'mean%':>9}{'t':>8}")
    print("-" * 55)
    for label, values in (
        ("pooled (event level)", pooled),
        ("date-clustered", clusters),
        ("first half (clustered)", first),
        ("second half (clustered)", second),
        ("placebo", placebo_pooled),
    ):
        stat = tstat(values)
        print(f"{label:<32}{len(values):>6}{statistics.mean(values):>9.2f}"
              f"{(stat if stat is not None else float('nan')):>8.2f}")
    print(f"{'median (event level)':<32}{len(pooled):>6}{statistics.median(pooled):>9.2f}")
    print(f"{'10% trimmed mean':<32}{len(pooled):>6}{trimmed(pooled):>9.2f}")
    for year_key in sorted(years):
        stat = tstat(years[year_key])
        print(f"  {year_key:<30}{len(years[year_key]):>6}{statistics.mean(years[year_key]):>9.2f}"
              f"{(stat if stat is not None else float('nan')):>8.2f}")

    checks: list[bool] = []
    h1, h2 = tstat(first), tstat(second)

    def check(label: str, ok: bool, detail: str) -> None:
        checks.append(ok)
        print(f"[{'PASS' if ok else 'FAIL'}] {label}: {detail}")

    check("1. both halves t >= 2",
          bool(h1 and h2 and h1 >= 2 and h2 >= 2),
          f"first t={h1:.2f}, second t={h2:.2f}" if h1 and h2 else "not computable")
    check("2. median and trimmed agree",
          statistics.median(pooled) > 0 and trimmed(pooled) > 0,
          f"mean {statistics.mean(pooled):+.2f}%, median {statistics.median(pooled):+.2f}%, "
          f"trimmed {trimmed(pooled):+.2f}%")
    spread = statistics.mean(pooled) - statistics.mean(placebo_pooled)
    check("3. beats placebo by >= 0.5pp", spread >= 0.5,
          f"{statistics.mean(pooled):+.2f}% vs {statistics.mean(placebo_pooled):+.2f}% "
          f"({spread:+.2f}pp, Welch t={(welch(pooled, placebo_pooled) or float('nan')):.2f})")
    check("4. positive after costs", statistics.mean(pooled) > 0,
          f"net {statistics.mean(pooled):+.2f}% after {COST_PCT:.2f}% round trip")
    print(f"-> {'PASSES' if all(checks) else 'FAILS'} ({checks.count(False)} of 4 failed)")
    return checks


def main() -> int:
    bull, bull_placebo, bear, bear_placebo = [], [], [], []
    for symbol in config.all_symbols():
        if config.asset_class(symbol) != "stock":
            continue
        try:
            candles = data.get_candles(symbol)
        except data.DataError as exc:
            print(f"skip {symbol}: {exc}")
            continue
        events = traps.detect(candles, lookback=20, volume_mult=1.2)
        placebo = placebo_events(candles, lookback=20, volume_mult=1.2)
        bull.append((candles, [e for e in events if e.side > 0]))
        bull_placebo.append((candles, [e for e in placebo if e.side > 0]))
        bear.append((candles, [e for e in events if e.side < 0]))
        bear_placebo.append((candles, [e for e in placebo if e.side < 0]))

    print("Trade under test: after a sweep that closes back inside the range, take the")
    print("opposite side for 5 bars from the next open. Costs included.")
    print("=" * 55)
    bullish_checks = evaluate("BULLISH SWEEPS -> short (pre-registered test)", bull, bull_placebo)
    bearish_checks = evaluate("BEARISH SWEEPS -> long (post-hoc second look)", bear, bear_placebo)

    print("\n" + "=" * 55)
    print("VERDICT")
    if all(bullish_checks):
        print("pre-registered test PASSES -- but validate forward before sizing.")
    else:
        print(f"pre-registered test FAILS ({bullish_checks.count(False)}/4). Do not wire either side")
        print("into scoring: the bearish result was found after seeing its t-stat, which is exactly")
        print("the selection effect this protocol exists to catch.")
    if all(bearish_checks) and not all(bullish_checks):
        print("\nThe bearish side is the only surviving lead. Treat it as a hypothesis for the")
        print("forward log, not as a signal -- one cell of 25, chosen after the fact.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
