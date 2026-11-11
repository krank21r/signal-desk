"""Long-term horizon: does weekly trend-following beat the daily engine?

Fetches ten years of daily bars directly (the app caps history at 1,000 bars for
the live view), resamples to weekly, and runs the same rules on both horizons
over the same window — so the comparison is like for like rather than
three-year daily against ten-year weekly.

Weekly EMA200 needs roughly four years of warmup, which is why the live app's
1,000-bar cap cannot run this test.

    python scripts/research_weekly.py
"""

from __future__ import annotations

import math
import statistics
import sys
from collections import OrderedDict, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import backtest, config, data, signals  # noqa: E402
from app.data import Candle  # noqa: E402


def fetch_daily_long(symbol: str) -> list[Candle]:
    """Ten years of daily bars from Yahoo, which serves the full range in one call."""
    payload = data._get_json(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
        {"range": "10y", "interval": "1d", "includePrePost": "false"},
    )
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        raise data.DataError("yahoo returned nothing")
    result = result[0]
    stamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []

    bars: list[Candle] = []
    for i, stamp in enumerate(stamps):
        try:
            o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        except IndexError:
            continue
        if None in (o, h, l, c) or c <= 0:
            continue
        v = volumes[i] if i < len(volumes) and volumes[i] is not None else 0.0
        bars.append(Candle(int(stamp), float(o), float(h), float(l), float(c), float(v)))
    if not bars:
        raise data.DataError("empty series")
    return bars


def to_weekly(daily: list[Candle]) -> list[Candle]:
    groups: OrderedDict[tuple[int, int], list[Candle]] = OrderedDict()
    for bar in daily:
        moment = datetime.fromtimestamp(bar.t, tz=timezone.utc)
        key = moment.isocalendar()[:2]
        groups.setdefault(key, []).append(bar)

    weekly: list[Candle] = []
    for bars in groups.values():
        weekly.append(
            Candle(
                t=bars[0].t,
                o=bars[0].o,
                h=max(b.h for b in bars),
                l=min(b.l for b in bars),
                c=bars[-1].c,
                v=sum(b.v for b in bars),
            )
        )
    return weekly


def pool(trades: list[dict], label: str) -> dict:
    if len(trades) < 10:
        print(f"  {label:<12} only {len(trades)} trades — skipped")
        return {}
    rets = [t["ret_pct"] for t in trades]
    rs = [t["r_multiple"] for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]

    buckets: dict[str, list[float]] = defaultdict(list)
    for trade in trades:
        buckets[trade["entry_date"]].append(trade["r_multiple"])
    clusters = [statistics.mean(v) for v in buckets.values()]
    c_sd = statistics.stdev(clusters) if len(clusters) > 2 else 0.0

    expectancy = statistics.mean(rs)
    sd = statistics.stdev(rs) if len(rs) > 2 else 0.0
    t_plain = expectancy / (sd / math.sqrt(len(rs))) if sd else 0.0
    t_clust = statistics.mean(clusters) / (c_sd / math.sqrt(len(clusters))) if c_sd else 0.0
    pf = (sum(wins) / abs(sum(losses))) if wins and losses else float("inf")

    print(
        f"  {label:<12} n={len(trades):>4}  win={len(wins) / len(rets) * 100:>5.1f}%  "
        f"exp={expectancy:+.3f}R  PF={pf:>4.2f}  hold={statistics.mean([t['bars'] for t in trades]):>5.1f}  "
        f"t={t_plain:+.2f}  t(clust)={t_clust:+.2f}"
    )
    return {"n": len(trades), "expectancy": expectancy, "t_clust": t_clust}


def main() -> int:
    original = dict(config.COSTS_BPS)
    # Weekly holds pay far less in costs; use the daily model for both so the
    # comparison isolates the horizon rather than the fee assumption.
    try:
        daily_trades: dict[str, list[dict]] = {"stock": [], "crypto": []}
        weekly_trades: dict[str, list[dict]] = {"stock": [], "crypto": []}
        spans: list[str] = []

        for symbol in config.all_symbols():
            kind = config.asset_class(symbol)
            try:
                daily = fetch_daily_long(symbol)
            except data.DataError as exc:
                print(f"skip {symbol}: {exc}")
                continue
            weekly = to_weekly(daily)
            if len(weekly) < 120:
                print(f"skip {symbol}: only {len(weekly)} weekly bars")
                continue
            spans.append(f"{symbol} {len(daily)}d/{len(weekly)}w")

            for bars, sink in ((daily, daily_trades), (weekly, weekly_trades)):
                points = signals.series(bars)
                if not points:
                    continue
                stats = backtest.run(bars, points, kind)
                sink[kind].extend(stats.get("trade_log", []))

        print(f"universe: {len(spans)} symbols  ({', '.join(spans[:3])} …)\n")
        print("=" * 96)
        print("DAILY · ten years")
        for kind in ("crypto", "stock"):
            pool(daily_trades[kind], kind)
        print("\nWEEKLY · ten years (same rules)")
        for kind in ("crypto", "stock"):
            pool(weekly_trades[kind], kind)
        print("=" * 96)
        print("Reference (live app, 1,000-bar cap ≈ 3y): daily crypto +0.246R t 2.69 · daily stocks +0.005R")
    finally:
        config.COSTS_BPS.clear()
        config.COSTS_BPS.update(original)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
