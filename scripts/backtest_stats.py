"""Pool every backtested trade across the universe and report win/loss.

What this answers that the per-symbol table cannot: across all ~500 trades the
engine produced on this history, how many won, how many lost, and by how much.

    python scripts/backtest_stats.py
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


def summarise(trades: list[dict], label: str) -> dict:
    rets = [t["ret_pct"] for t in trades]
    rs = [t["r_multiple"] for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    win_rate = len(wins) / len(rets) * 100 if rets else 0.0
    avg_win = statistics.mean(wins) if wins else 0.0
    avg_loss = statistics.mean(losses) if losses else 0.0
    pf = (sum(wins) / abs(sum(losses))) if wins and losses else float("inf")
    expectancy_r = statistics.mean(rs) if rs else 0.0
    sd = statistics.stdev(rs) if len(rs) > 2 else 0.0
    t_stat = (expectancy_r / (sd / math.sqrt(len(rs)))) if sd else 0.0

    # Trades opened on the same day are not independent: twelve symbols selling
    # off together is one observation, not twelve. Cluster by entry date and
    # test the averages, which is the conservative version of t.
    buckets: dict[str, list[float]] = defaultdict(list)
    for trade in trades:
        buckets[trade["entry_date"]].append(trade["r_multiple"])
    clusters = [statistics.mean(v) for v in buckets.values()]
    c_sd = statistics.stdev(clusters) if len(clusters) > 2 else 0.0
    t_clustered = (
        (statistics.mean(clusters) / (c_sd / math.sqrt(len(clusters)))) if c_sd else 0.0
    )

    print(f"\n{label}")
    print(f"  trades           {len(trades)}")
    print(f"  wins             {len(wins)}  ({win_rate:.1f}%)")
    print(f"  losses           {len(losses)}")
    print(f"  avg win          {avg_win:+.2f}%")
    print(f"  avg loss         {avg_loss:+.2f}%")
    print(f"  payoff           {abs(avg_win / avg_loss):.2f}x" if avg_loss else "  payoff           n/a")
    print(f"  expectancy       {statistics.mean(rets):+.3f}%  = {expectancy_r:+.3f}R")
    print(f"  profit factor    {pf:.2f}")
    print(f"  t-stat           {t_stat:+.2f}")
    print(f"  t (date-clustered) {t_clustered:+.2f}   over {len(clusters)} distinct entry dates")
    print(f"  best / worst     {max(rets):+.1f}% / {min(rets):+.1f}%")
    return {"n": len(trades), "win_rate": win_rate, "expectancy_r": expectancy_r}


def main() -> int:
    pooled: list[dict] = []
    per_symbol: list[tuple[str, list[dict]]] = []

    for symbol in config.all_symbols():
        try:
            candles = data.get_candles(symbol)
        except data.DataError as exc:
            print(f"skip {symbol}: {exc}")
            continue
        points = signals.series(candles)
        stats = backtest.run(candles, points, config.asset_class(symbol))
        trades = stats.get("trade_log", [])
        pooled.extend({**trade, "symbol": symbol} for trade in trades)
        per_symbol.append((symbol, trades))

    if not pooled:
        print("no trades produced")
        return 1

    print(f"universe: {len(per_symbol)} symbols, {len(pooled)} closed trades")
    summarise(pooled, "ALL TRADES")
    summarise([t for t in pooled if t["side"] == "LONG"], "LONG ONLY")
    summarise([t for t in pooled if t["side"] == "SHORT"], "SHORT ONLY")
    summarise([t for t in pooled if t["symbol"].endswith("-USD")], "CRYPTO")
    summarise([t for t in pooled if not t["symbol"].endswith("-USD")], "STOCKS")
    summarise([t for t in pooled if t["target_hit"]], "TARGET HIT")
    summarise([t for t in pooled if not t["target_hit"]], "NO TARGET")

    print("\nPER SYMBOL")
    print(f"{'symbol':<10}{'trades':>7}{'win%':>7}{'expR':>8}{'avgW':>8}{'avgL':>8}")
    print("-" * 48)
    for symbol, trades in per_symbol:
        if not trades:
            continue
        rets = [t["ret_pct"] for t in trades]
        wins = [r for r in rets if r > 0]
        losses = [r for r in rets if r <= 0]
        print(
            f"{symbol:<10}{len(trades):>7}{len(wins) / len(trades) * 100:>7.1f}"
            f"{statistics.mean([t['r_multiple'] for t in trades]):>8.2f}"
            f"{(statistics.mean(wins) if wins else 0):>8.2f}"
            f"{(statistics.mean(losses) if losses else 0):>8.2f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
