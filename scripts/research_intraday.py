"""Does the daily engine work on 1-hour bars?

Same rules, same exit logic, hourly bars, and a harsher cost model — because an
hourly strategy pays the spread twenty-four times a day instead of once.

    python scripts/research_intraday.py
"""

from __future__ import annotations

import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import backtest, config, data, signals  # noqa: E402


def summarise(trades: list[dict], label: str) -> None:
    if len(trades) < 5:
        print(f"\n{label}: only {len(trades)} trades — not enough to judge.")
        return
    rets = [t["ret_pct"] for t in trades]
    rs = [t["r_multiple"] for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    expect = statistics.mean(rs)
    sd = statistics.stdev(rs)
    t_plain = expect / (sd / math.sqrt(len(rs))) if sd else 0.0

    buckets: dict[str, list[float]] = defaultdict(list)
    for trade in trades:
        buckets[trade["entry_date"]].append(trade["r_multiple"])
    clusters = [statistics.mean(v) for v in buckets.values()]
    c_sd = statistics.stdev(clusters) if len(clusters) > 2 else 0.0
    t_clustered = statistics.mean(clusters) / (c_sd / math.sqrt(len(clusters))) if c_sd else 0.0

    pf = (sum(wins) / abs(sum(losses))) if wins and losses else float("inf")
    hold = statistics.mean([t["bars"] for t in trades])
    print(
        f"\n{label}\n"
        f"  trades {len(trades):>5}   win {len(wins) / len(rets) * 100:>5.1f}%   "
        f"expectancy {expect:+.3f}R   PF {pf:.2f}\n"
        f"  avg hold {hold:.1f} bars   t {t_plain:+.2f}   "
        f"t (clustered, {len(clusters)} dates) {t_clustered:+.2f}"
    )


def main() -> int:
    original_costs = dict(config.COSTS_BPS)
    config.COSTS_BPS.update(config.INTRADAY_COSTS_BPS)

    pooled: dict[str, list[dict]] = {"stock": [], "crypto": []}
    try:
        for symbol in config.all_symbols():
            kind = config.asset_class(symbol)
            try:
                bars = data.get_intraday(symbol)
            except data.DataError as exc:
                print(f"skip {symbol}: {exc}")
                continue
            points = signals.series(bars)
            if not points:
                print(f"skip {symbol}: not enough bars ({len(bars)})")
                continue
            stats = backtest.run(bars, points, kind)
            trades = stats.get("trade_log", [])
            for trade in trades:
                pooled[kind].append({**trade, "symbol": symbol})
            span = f"{bars[0].t}..{bars[-1].t}"
            print(f"{symbol:<10} bars {len(bars):>5}  trades {len(trades):>4}  span {span}")
    finally:
        config.COSTS_BPS.clear()
        config.COSTS_BPS.update(original_costs)

    print("\n" + "=" * 70)
    print(f"costs: stock {config.INTRADAY_COSTS_BPS['stock']}bps, "
          f"crypto {config.INTRADAY_COSTS_BPS['crypto']}bps per side")
    print("=" * 70)
    summarise(pooled["crypto"], "CRYPTO · 1h")
    summarise(pooled["stock"], "STOCKS · 1h")
    summarise(pooled["crypto"] + pooled["stock"], "ALL · 1h")

    print(
        "\nFor reference, the same rules on DAILY bars scored "
        f"{0.118:+.3f}R over 468 trades (crypto +0.246R, stocks +0.005R)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
