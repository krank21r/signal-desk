"""How sensitive is the trail distance?

Runs the same signals over the whole universe with different TRAIL_ATR values
and prints the trade-off. This is a sensitivity check, not a parameter search:
picking the row with the highest expectancy here is exactly how a backtest gets
overfitted to its own history.

    python scripts/sweep_trail.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import backtest, config, data, signals  # noqa: E402

TRAILS = (1.5, 2.0, 2.5, 3.0, 4.0, 5.0)
KEYS = ("expectancy_r", "win_rate", "profit_factor", "total_return_pct",
        "max_drawdown_pct", "trades", "avg_bars_held")


def mean(values):
    clean = [v for v in values if v is not None]
    return sum(clean) / len(clean) if clean else None


def main() -> int:
    symbols = config.all_symbols()
    cached = []
    for symbol in symbols:
        try:
            candles = data.get_candles(symbol)
        except data.DataError as exc:
            print(f"skip {symbol}: {exc}")
            continue
        cached.append((symbol, config.asset_class(symbol), candles, signals.series(candles)))

    original = config.TRAIL_ATR
    rows = []
    try:
        for trail in TRAILS:
            config.TRAIL_ATR = trail
            results = [
                backtest.run(candles, points, kind, exit_mode="partial")
                for _, kind, candles, points in cached
            ]
            rows.append((trail, results))
    finally:
        config.TRAIL_ATR = original

    header = f"{'trail':>6}" + "".join(f"{key.replace('_pct', ''):>14}" for key in KEYS)
    print(f"universe: {len(cached)} symbols, EXIT_MODE=partial, stop={config.STOP_ATR}xATR "
          f"target={config.TARGET_ATR}xATR\n")
    print(header)
    print("-" * len(header))
    for trail, results in rows:
        line = f"{trail:>6.1f}"
        for key in KEYS:
            value = mean([r.get(key) for r in results])
            line += f"{value:>14.2f}" if isinstance(value, (int, float)) else f"{'—':>14}"
        print(line)

    print("\nnote: expectancy and drawdown are averages across symbols, not a portfolio.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
