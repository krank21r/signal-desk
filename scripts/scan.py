"""Terminal view of the signal table.

    python scripts/scan.py                 # everything
    python scripts/scan.py --class crypto  # crypto only
    python scripts/scan.py --symbol NVDA   # one symbol, with votes and stats

Handy when you want the numbers without a browser, and a quick way to confirm
the data feeds are reachable.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import backtest, config, data, service, signals  # noqa: E402


def fmt(value, digits=2, suffix=""):
    if value is None:
        return "—"
    if isinstance(value, (int, float)):
        return f"{value:,.{digits}f}{suffix}"
    return f"{value}{suffix}"


def print_table(asset_class: str) -> int:
    payload = service.scan(kind=asset_class)
    rows = payload["rows"]
    header = f"{'SYMBOL':<10}{'SIGNAL':<8}{'SCORE':>7}{'LAST':>12}{'1D':>8}{'30D':>8}{'ENTRY':>11}{'STOP':>11}{'TARGET':>11}{'WIN%':>7}{'EXPR':>7}{'N':>5}"
    print(header)
    print("-" * len(header))
    for row in rows:
        bt = row["backtest"]
        entry = row.get("entry")
        print(
            f"{row['symbol']:<10}{row['signal']:<8}{fmt(row['score']):>7}"
            f"{fmt(row['price']):>12}{fmt(row['change_1d'], 1, '%'):>8}{fmt(row['change_30d'], 1, '%'):>8}"
            f"{fmt(entry):>11}{fmt(row.get('stop')):>11}{fmt(row.get('target') or row.get('target_banked')):>11}"
            f"{fmt(bt['win_rate'], 1):>7}{fmt(bt['expectancy_r']):>7}{fmt(bt['trades'], 0):>5}"
        )
    if payload["errors"]:
        print("\nunavailable:", ", ".join(payload["errors"]))
    counts = payload["counts"]
    print(f"\n{counts['long']} long · {counts['short']} short · {counts['flat']} flat")
    return 0


def print_symbol(symbol: str) -> int:
    analysis = service.analyze(symbol)
    summary = analysis.summary
    stats = analysis.stats

    print(f"{symbol} — {analysis.name} ({analysis.kind})")
    print(f"  bars        {len(analysis.candles)}  {stats.get('start')} → {stats.get('end')}")
    print(f"  signal      {summary['signal']}  score {summary['score']}  ({summary['conviction']} conviction)")
    print(f"  price       {fmt(summary['price'])}")
    if summary.get("blocked"):
        print(f"  gated       {summary['block_reason']}")
    if summary.get("entry"):
        print(
            f"  position    entry {fmt(summary['entry'])}  stop {fmt(summary.get('stop'))}  "
            f"target {fmt(summary.get('target'))}  {fmt(summary.get('unrealized_pct'), 2, '%')}"
        )
    print("\n  votes")
    for vote in summary.get("votes", []):
        print(f"    {vote['label']:<16}{vote['vote']:+d}   {vote['detail']}")
    print("\n  backtest")
    for key in (
        "trades",
        "win_rate",
        "expectancy_r",
        "profit_factor",
        "total_return_pct",
        "buy_hold_pct",
        "max_drawdown_pct",
        "sharpe",
        "t_stat",
        "exposure_pct",
    ):
        print(f"    {key:<18}{fmt(stats.get(key))}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Signal Desk terminal view")
    parser.add_argument("--class", dest="asset_class", default="all", choices=["all", "stock", "crypto"])
    parser.add_argument("--symbol", help="show one symbol in detail")
    args = parser.parse_args()
    if args.symbol:
        try:
            return print_symbol(args.symbol.upper())
        except data.DataError as exc:
            print(f"data unavailable: {exc}", file=sys.stderr)
            return 2
    return print_table(args.asset_class)


if __name__ == "__main__":
    raise SystemExit(main())
