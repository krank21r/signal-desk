"""Compare exit rules across the whole universe.

Answers one question: with identical entries, does a trailing stop beat a fixed
1.5R target? Run it after changing any parameter -- it reads the same engine the
app uses, so the comparison cannot drift from what the UI shows.

    python scripts/compare_exits.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import backtest, config, data, signals  # noqa: E402

MODES = ("fixed", "trailing", "partial")
KEYS = ("trades", "win_rate", "expectancy_r", "expectancy_pct", "profit_factor",
        "total_return_pct", "max_drawdown_pct", "sharpe", "t_stat", "avg_bars_held",
        "target_hit_rate")


def mean(values):
    clean = [v for v in values if v is not None]
    return sum(clean) / len(clean) if clean else None


def main() -> int:
    symbols = config.all_symbols()
    results: dict[str, list[dict]] = {mode: [] for mode in MODES}

    for symbol in symbols:
        try:
            candles = data.get_candles(symbol)
        except data.DataError as exc:
            print(f"skip {symbol}: {exc}")
            continue
        points = signals.series(candles)
        for mode in MODES:
            results[mode].append(backtest.run(candles, points, config.asset_class(symbol), exit_mode=mode))

    print(f"universe: {len(results['fixed'])} symbols\n")
    header = f"{'metric':<20}" + "".join(f"{mode:>12}" for mode in MODES)
    print(header)
    print("-" * len(header))
    for key in KEYS:
        row = f"{key:<20}"
        for mode in MODES:
            value = mean([r.get(key) for r in results[mode]])
            row += f"{value:>12.2f}" if isinstance(value, (int, float)) else f"{'—':>12}"
        print(row)

    for mode in MODES:
        positive = sum(1 for r in results[mode] if (r.get("expectancy_r") or 0) > 0)
        beat_hold = sum(1 for r in results[mode] if (r.get("total_return_pct") or 0) > (r.get("buy_hold_pct") or 0))
        print(f"\n{mode:<20} positive expectancy: {positive}/{len(results[mode])}"
              f"   beats buy&hold: {beat_hold}/{len(results[mode])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
