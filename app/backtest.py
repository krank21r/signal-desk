"""Bar-by-bar backtest of the signal engine.

Rules, stated once so the numbers can be audited:

* A signal computed on bar ``i`` is executed at the **open of bar i+1**. No
  signal can trade on the bar that produced it.
* Stops and targets are checked against each bar's high/low. Gaps through a
  level fill at the open, not at the level.
* Costs are charged per side (``config.COSTS_BPS``) on entry and exit, sized as
  an adverse fill: buys pay up, sells give up. A partial exit pays its share.
* Position sizing is one unit of equity, long or short, no leverage. Short P&L
  is linear; borrow cost and financing are not modelled.
* Three exit rules, selected by ``config.EXIT_MODE``:

  ``fixed``    initial 2xATR stop, hard 3xATR target, full exit.
  ``trailing`` initial 2xATR stop, then 3xATR chandelier trail, no target.
  ``partial``  initial 2xATR stop, take half off at the 3xATR target, then
               trail the remainder 3xATR behind the best price.

* A round trip is reported as **one trade**, even when it exits in two pieces:
  the exit price is the size-weighted average and the R multiple is the sum of
  the legs. That keeps trade statistics comparable across exit modes.
* The equity curve is marked to market every bar. Trade statistics come from
  the closed-trade ledger. Both are reported and they reconcile at each exit.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Optional, Sequence

from . import config
from .data import Candle
from .signals import SignalPoint


@dataclass
class Trade:
    side: int
    entry_t: int
    exit_t: int
    entry: float
    exit: float
    ret_pct: float
    r_multiple: float
    bars: int
    reason: str
    target_hit: bool = False

    def as_dict(self) -> dict:
        d = asdict(self)
        d["side"] = "LONG" if self.side > 0 else "SHORT"
        d["entry_date"] = _date(self.entry_t)
        d["exit_date"] = _date(self.exit_t)
        return d


def _date(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _stdev(values: Sequence[float]) -> Optional[float]:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return var ** 0.5


def run(
    candles: Sequence[Candle],
    points: Sequence[SignalPoint],
    kind: str,
    exit_mode: str | None = None,
) -> dict:
    """Backtest a signal series against its own history."""
    n = len(candles)
    if n < 60 or not points:
        return {"error": "insufficient history"}

    mode = (exit_mode or config.EXIT_MODE).lower()
    if mode not in ("fixed", "trailing", "partial"):
        raise ValueError(f"unknown exit mode: {mode}")
    wants_target = mode in ("fixed", "partial")
    trails = mode in ("trailing", "partial")

    fee = config.COSTS_BPS.get(kind, 2.0) / 10_000.0
    stop_mult = config.STOP_ATR
    target_mult = config.TARGET_ATR

    position = 0
    size = 0.0  # fraction of the original unit still open
    entry_price = 0.0
    entry_i = 0
    entry_t = 0
    entry_atr = 0.0
    best = 0.0
    stop = 0.0
    target = 0.0
    target_initial = 0.0
    risk_per_unit = 0.0
    partial_taken = False
    legs: list[tuple[float, float, float, str]] = []  # (weight, exit fill, ret, reason)
    pending: Optional[tuple[int, float]] = None  # (side, atr at signal bar)

    equity = 1.0
    curve: list[dict] = []
    trades: list[Trade] = []
    exposure_bars = 0

    by_index = {p.i: p for p in points}

    def book_leg(fill: float, weight: float, reason: str, exit_index: int, exit_t: int) -> None:
        """Realise ``weight`` of the position at ``fill`` and record the leg."""
        nonlocal equity, legs, size, partial_taken, target
        exit_adj = fill * (1 - position * fee)
        ret = (exit_adj / entry_price - 1) * position
        equity *= (1 + weight * ret) * (1 - weight * fee)
        legs.append((weight, exit_adj, ret, reason))
        size -= weight
        if "target" in reason:
            partial_taken = True
            target = 0.0  # the objective is done; the rest rides the trail
        if size <= 1e-9:
            _close_round_trip(exit_index, exit_t)

    def _close_round_trip(exit_index: int, exit_t: int) -> None:
        nonlocal position, size, legs, entry_price, entry_i, entry_t
        nonlocal entry_atr, best, stop, target, target_initial, risk_per_unit, partial_taken
        if not legs:
            return
        total_weight = sum(w for w, _, _, _ in legs) or 1.0
        avg_exit = sum(w * fill for w, fill, _, _ in legs) / total_weight
        ret = sum(w * r for w, _, r, _ in legs) / total_weight
        r_multi = (
            sum(w * ((fill - entry_price) * position / risk_per_unit) for w, fill, _, _ in legs)
            / total_weight
            if risk_per_unit
            else 0.0
        )
        reason = " + ".join(dict.fromkeys(reason for _, _, _, reason in legs))
        trades.append(
            Trade(
                side=position,
                entry_t=entry_t,
                exit_t=exit_t,
                entry=round(entry_price, 6),
                exit=round(avg_exit, 6),
                ret_pct=round(ret * 100, 3),
                r_multiple=round(r_multi, 2),
                bars=exit_index - entry_i,
                reason=reason,
                target_hit=any("target" in r for _, _, _, r in legs),
            )
        )
        position = 0
        size = 0.0
        legs = []
        entry_price = stop = target = risk_per_unit = best = 0.0
        target_initial = 0.0
        partial_taken = False

    for i in range(n):
        bar = candles[i]
        flat_prev_close = candles[i - 1].c if i > 0 else bar.o

        # --- 1. execute a pending move at this bar's open ------------------
        just_entered = False
        if pending is not None:
            desired, atr_at_signal = pending
            pending = None
            # A decisive reversal closes the old side and opens the new one at
            # the same open, so a -1 -> +1 signal flip is actually tradeable
            # instead of being ignored while the old position sits there.
            if position != 0 and desired != position:
                book_leg(bar.o, size, "signal flip" if desired != 0 else "signal exit", i, bar.t)
            if position == 0 and desired != 0 and atr_at_signal > 0:
                side = desired
                position = side
                size = 1.0
                legs = []
                partial_taken = False
                entry_price = bar.o * (1 + side * fee)  # adverse fill on entry
                entry_i, entry_t = i, bar.t
                entry_atr = atr_at_signal
                best = entry_price
                risk_per_unit = stop_mult * atr_at_signal
                stop = entry_price - side * risk_per_unit
                target = entry_price + side * target_mult * atr_at_signal if wants_target else 0.0
                target_initial = target
                equity *= 1 - fee  # entry commission on the full unit
                just_entered = True

        # --- 2. manage an open position with this bar's range --------------
        # Stops are live on the entry bar too: the level is set from the fill
        # price at this bar's open, so a same-bar reversal really does stop out.
        if position != 0:
            exit_fill = None
            reason = ""
            hit_target = False
            if position > 0:
                if bar.o <= stop:
                    exit_fill, reason = bar.o, "gap through stop"
                elif bar.l <= stop:
                    exit_fill, reason = stop, "stop"
                elif wants_target and target and bar.o >= target:
                    exit_fill, reason, hit_target = bar.o, "gap through target", True
                elif wants_target and target and bar.h >= target:
                    exit_fill, reason, hit_target = target, "target", True
            else:
                if bar.o >= stop:
                    exit_fill, reason = bar.o, "gap through stop"
                elif bar.h >= stop:
                    exit_fill, reason = stop, "stop"
                elif wants_target and target and bar.o <= target:
                    exit_fill, reason, hit_target = bar.o, "gap through target", True
                elif wants_target and target and bar.l <= target:
                    exit_fill, reason, hit_target = target, "target", True

            if exit_fill is not None:
                if hit_target and mode == "partial":
                    book_leg(exit_fill, config.PARTIAL_FRACTION, "target (half)", i, bar.t)
                    # The survivor now rides a trail from the best price so far.
                else:
                    book_leg(exit_fill, size, reason, i, bar.t)

            # Trailing exit: ratchet the stop behind the best price, but only
            # after this bar's stop check. Using the same bar's extreme to set a
            # level that the same bar then trips would be intrabar look-ahead.
            if position != 0 and trails:
                if position > 0:
                    best = max(best, bar.h)
                else:
                    best = min(best, bar.l)
                if mode == "trailing" or partial_taken:
                    if position > 0:
                        stop = max(stop, best - config.TRAIL_ATR * entry_atr)
                    else:
                        stop = min(stop, best + config.TRAIL_ATR * entry_atr)

        # --- 3. queue the next action from this bar's signal ---------------
        # The signal series is the target position; anything different from what
        # we hold is acted on at the next open.
        point = by_index.get(i)
        if point is not None and point.atr and i + 1 < n and point.position != position:
            pending = (point.position, point.atr)

        # --- 4. mark to market ---------------------------------------------
        if position != 0:
            exposure_bars += 1
            if just_entered:
                bar_ret = (bar.c / bar.o - 1) * position  # entered at the open
            else:
                bar_ret = (bar.c / flat_prev_close - 1) * position
            equity_mtm = equity * (1 + size * bar_ret)
        else:
            equity_mtm = equity

        curve.append({"t": bar.t, "equity": round(equity_mtm, 6), "position": position})

    # Capture the position still live at the final bar, with the stop and target
    # as they actually stand. This is what the UI shows; the forced close below
    # only feeds the statistics.
    live = None
    if position != 0:
        last = candles[-1]
        live = {
            "side": position,
            "size": round(size, 2),
            "entry": round(entry_price, 6),
            "stop": round(stop, 6),
            "target": round(target, 6) if target else None,
            "target_initial": round(target_initial, 6) if target_initial else None,
            "entry_date": _date(entry_t),
            "bars": len(candles) - 1 - entry_i,
            "unrealized_pct": round((last.c / entry_price - 1) * 100 * position, 2),
            "unrealized_r": round((last.c - entry_price) * position / risk_per_unit, 2)
            if risk_per_unit
            else None,
            "partial_taken": partial_taken,
        }
        book_leg(last.c, size, "open at end", n - 1, last.t)

    # ------------------------------------------------------------------
    # statistics
    # ------------------------------------------------------------------
    ppy = config.PERIODS_PER_YEAR.get(kind, 252)
    rets = [t.ret_pct for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]

    total_return = (equity - 1) * 100
    days = max((candles[-1].t - candles[0].t) / 86400.0, 1.0)
    years = days / 365.0
    cagr = ((equity ** (1 / years)) - 1) * 100 if equity > 0 and years > 0 else None

    peak = curve[0]["equity"] if curve else 1.0
    max_dd = 0.0
    for row in curve:
        peak = max(peak, row["equity"])
        if peak > 0:
            max_dd = min(max_dd, row["equity"] / peak - 1)

    daily = []
    for a, b in zip(curve, curve[1:]):
        if a["equity"] > 0:
            daily.append(b["equity"] / a["equity"] - 1)
    sd = _stdev(daily)
    sharpe = (sum(daily) / len(daily)) / sd * (ppy ** 0.5) if daily and sd else None

    trade_sd = _stdev(rets)
    t_stat = (sum(rets) / len(rets)) / (trade_sd / (len(rets) ** 0.5)) if trade_sd and len(rets) > 2 else None

    long_trades = [t for t in trades if t.side > 0]
    short_trades = [t for t in trades if t.side < 0]
    target_hits = [t for t in trades if t.target_hit]

    return {
        "trades": len(trades),
        "win_rate": round(len(wins) / len(rets) * 100, 1) if rets else None,
        "avg_win_pct": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss_pct": round(sum(losses) / len(losses), 2) if losses else None,
        "payoff": round((sum(wins) / len(wins)) / abs(sum(losses) / len(losses)), 2)
        if wins and losses
        else None,
        "expectancy_pct": round(sum(rets) / len(rets), 3) if rets else None,
        "expectancy_r": round(sum(t.r_multiple for t in trades) / len(trades), 3) if trades else None,
        "profit_factor": round(sum(wins) / abs(sum(losses)), 2) if wins and losses else None,
        "total_return_pct": round(total_return, 2),
        "cagr_pct": round(cagr, 2) if cagr is not None else None,
        "max_drawdown_pct": round(max_dd * 100, 2),
        "sharpe": round(sharpe, 2) if sharpe is not None else None,
        "t_stat": round(t_stat, 2) if t_stat is not None else None,
        "exposure_pct": round(exposure_bars / n * 100, 1),
        "avg_bars_held": round(sum(t.bars for t in trades) / len(trades), 1) if trades else None,
        "best_trade_pct": round(max(rets), 2) if rets else None,
        "worst_trade_pct": round(min(rets), 2) if rets else None,
        "target_hit_rate": round(len(target_hits) / len(trades) * 100, 1) if trades else None,
        "long_trades": len(long_trades),
        "long_win_rate": round(sum(1 for t in long_trades if t.ret_pct > 0) / len(long_trades) * 100, 1)
        if long_trades
        else None,
        "short_trades": len(short_trades),
        "short_win_rate": round(sum(1 for t in short_trades if t.ret_pct > 0) / len(short_trades) * 100, 1)
        if short_trades
        else None,
        "buy_hold_pct": round((candles[-1].c / candles[0].c - 1) * 100, 2),
        "start": _date(candles[0].t),
        "end": _date(candles[-1].t),
        "cost_bps_per_side": config.COSTS_BPS.get(kind, 2.0),
        "exit_mode": mode,
        "live": live,
        "equity_curve": curve,
        # The full ledger, not a tail: pooling across symbols needs every trade.
        # The drawer trims to the ten most recent for display.
        "trade_log": [t.as_dict() for t in trades],
    }
