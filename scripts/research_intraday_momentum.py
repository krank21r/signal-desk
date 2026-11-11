"""Intraday session momentum: does the first hour predict the rest of the day?

A different question from "port the daily rules to hourly bars". This tests the
documented intraday-momentum effect at the resolution we actually have: take the
first hourly bar of each session, and hold that direction to the session close.

No stop, no target — a raw directional edge, which is what an intraday strategy
needs before any exit logic can help it.

    python scripts/research_intraday_momentum.py
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

from app import config, data  # noqa: E402

MIN_BARS_PER_DAY = 4


def day_key(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def collect(bars, cost_pct: float) -> tuple[list[float], list[float], list[float]]:
    """Returns (momentum trade returns, unconditional session returns, first-bar moves)."""
    sessions: dict[str, list] = defaultdict(list)
    for bar in bars:
        sessions[day_key(bar.t)].append(bar)

    trade_rets: list[float] = []
    baseline_rets: list[float] = []
    first_moves: list[float] = []

    for bars_of_day in sessions.values():
        if len(bars_of_day) < MIN_BARS_PER_DAY:
            continue
        first = bars_of_day[0]
        if first.o <= 0:
            continue
        first_ret = (first.c / first.o - 1) * 100
        first_moves.append(first_ret)

        # Momentum: follow the first bar's direction into the close.
        entry = first.c
        exit_ = bars_of_day[-1].c
        session_ret = (exit_ / entry - 1) * 100
        trade_rets.append((1 if first_ret > 0 else -1) * session_ret - cost_pct)

        # Baseline: just hold the session from the first bar's close.
        baseline_rets.append(session_ret - cost_pct)

    return trade_rets, baseline_rets, first_moves


def report(values: list[float], label: str, index: dict[str, list[float]]) -> None:
    if len(values) < 20:
        print(f"{label}: too few ({len(values)})")
        return
    mean = statistics.mean(values)
    sd = statistics.stdev(values)
    t_plain = mean / (sd / math.sqrt(len(values))) if sd else 0.0

    # One observation per calendar date, averaged across symbols.
    clusters = [statistics.mean(v) for v in index.values()]
    c_sd = statistics.stdev(clusters) if len(clusters) > 2 else 0.0
    t_clustered = statistics.mean(clusters) / (c_sd / math.sqrt(len(clusters))) if c_sd else 0.0

    hit = sum(1 for v in values if v > 0) / len(values) * 100
    print(
        f"{label:<26} n={len(values):>5}  hit={hit:>5.1f}%  mean={mean:+.3f}%  "
        f"t={t_plain:+.2f}  t(clust)={t_clustered:+.2f}"
    )


def main() -> int:
    for kind in ("crypto", "stock"):
        cost_pct = 2 * config.INTRADAY_COSTS_BPS[kind] / 100.0  # round trip, in percent
        print(f"\n=== {kind} · round-trip cost {cost_pct:.2f}% ===")
        momentum_all: list[float] = []
        by_date: dict[str, list[float]] = defaultdict(list)
        baseline_all: list[float] = []

        for symbol in config.all_symbols():
            if config.asset_class(symbol) != kind:
                continue
            try:
                bars = data.get_intraday(symbol)
            except data.DataError as exc:
                print(f"  skip {symbol}: {exc}")
                continue
            trade_rets, baseline, _ = collect(bars, cost_pct)
            momentum_all.extend(trade_rets)
            baseline_all.extend(baseline)
            days: dict[str, list] = defaultdict(list)
            for bar in bars:
                days[day_key(bar.t)].append(bar)
            for key, bars_of_day in days.items():
                if len(bars_of_day) < MIN_BARS_PER_DAY:
                    continue
                first = bars_of_day[0]
                if first.o <= 0:
                    continue
                direction = 1 if first.c > first.o else -1
                by_date[key].append(direction * (bars_of_day[-1].c / first.c - 1) * 100 - cost_pct)

        report(momentum_all, "session momentum", by_date)
        if baseline_all:
            mean = statistics.mean(baseline_all)
            sd = statistics.stdev(baseline_all) if len(baseline_all) > 2 else 0
            print(
                f"{'unconditional long':<26} n={len(baseline_all):>5}  "
                f"mean={mean:+.3f}%  t={mean / (sd / math.sqrt(len(baseline_all))):+.2f}"
                if sd
                else ""
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
