"""Analysis orchestration: fetch -> signals -> backtest, with in-memory caching."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from . import backtest, config, data, indicators, signals
from . import journal
from .data import Candle
from .signals import SignalPoint


@dataclass
class Analysis:
    symbol: str
    kind: str
    name: str
    candles: list[Candle]
    points: list[SignalPoint]
    summary: dict
    stats: dict
    computed_at: float


_lock = threading.Lock()
_cache: dict[str, Analysis] = {}


def analyze(symbol: str, refresh: bool = False) -> Analysis:
    """Full analysis for one symbol, cached for ANALYSIS_TTL_SECONDS."""
    now = time.time()
    with _lock:
        hit = _cache.get(symbol)
    if hit and not refresh and now - hit.computed_at < config.ANALYSIS_TTL_SECONDS:
        return hit

    kind = config.asset_class(symbol)
    candles = data.get_candles(symbol, use_cache=not refresh)
    points = signals.series(candles)
    summary = signals.summarize(points, candles)
    stats = backtest.run(candles, points, kind)
    summary = _align_with_execution(summary, stats, candles)

    analysis = Analysis(
        symbol=symbol,
        kind=kind,
        name=config.display_name(symbol),
        candles=candles,
        points=points,
        summary=summary,
        stats=stats,
        computed_at=now,
    )
    with _lock:
        _cache[symbol] = analysis
    return analysis


def _align_with_execution(summary: dict, stats: dict, candles: list[Candle]) -> dict:
    """Make the headline signal match what the risk rules would actually hold.

    ``signals.summarize`` reads the close-based stance. The backtest also owns
    the stops, so a position can already have been closed even while the stance
    still reads long or short. Whichever position the backtest holds at the
    final bar wins, including its trailed stop.
    """
    live = stats.get("live")
    last_close = candles[-1].c if candles else None

    if live:
        summary["signal"] = "LONG" if live["side"] > 0 else "SHORT"
        summary["entry"] = live["entry"]
        summary["stop"] = live["stop"]
        summary["entry_date"] = live["entry_date"]
        summary["bars_held"] = live["bars"] + 1
        summary["unrealized_pct"] = live["unrealized_pct"]
        summary["unrealized_r"] = live["unrealized_r"]
        summary.pop("preview", None)
        # summarise() worked these out from a fresh-entry assumption; the live
        # position is authoritative, so drop them before deciding again.
        summary.pop("partial", None)
        summary.pop("trail_atr", None)
        summary.pop("partial_done", None)
        if live.get("target"):
            summary["target"] = live["target"]
            summary["rr"] = round(config.TARGET_ATR / config.STOP_ATR, 2)
        else:
            summary.pop("target", None)
            summary.pop("rr", None)
            if live.get("target_initial"):
                # The objective was already filled; keep the price it filled at
                # so the UI can say what was banked instead of showing nothing.
                summary["target_banked"] = live["target_initial"]
        if last_close and live["stop"]:
            summary["risk_pct"] = round(abs(last_close - live["stop"]) / last_close * 100, 2)
        if config.EXIT_MODE == "partial":
            if live.get("partial_taken"):
                summary["trail_atr"] = config.TRAIL_ATR
                summary["partial_done"] = True
            else:
                summary["partial"] = config.PARTIAL_FRACTION
                summary["partial_done"] = False
        elif config.EXIT_MODE == "trailing":
            summary["trail_atr"] = config.TRAIL_ATR
        return summary

    if summary.get("signal") in ("LONG", "SHORT"):
        last_trade = (stats.get("trade_log") or [None])[-1]
        reason = last_trade.get("reason") if last_trade else None
        stance = str(summary.get("bias", "neutral")).replace("_", " ")
        summary["signal"] = "FLAT"
        summary["exit_note"] = (
            f"model stance is {stance}, but the position was already closed by {reason or 'a risk exit'}"
        )
        summary.pop("preview", None)
    return summary


def _change_pct(candles: list[Candle], bars: int) -> Optional[float]:
    if len(candles) <= bars:
        return None
    then = candles[-1 - bars].c
    if not then:
        return None
    return round((candles[-1].c / then - 1) * 100, 2)


def row(analysis: Analysis) -> dict:
    """One row of the signal table."""
    s = analysis.summary
    stats = analysis.stats
    candles = analysis.candles
    levels = {
        k: s.get(k) for k in ("entry", "stop", "target", "risk_pct", "rr", "trail_atr") if s.get(k) is not None
    }
    if not levels and s.get("preview"):
        levels = {
            "entry": round(s.get("price", 0), 6),
            **{
                k: v
                for k, v in s["preview"].items()
                if k in ("stop", "target", "risk_pct", "rr", "trail_atr")
            },
        }
    return {
        "symbol": analysis.symbol,
        "name": analysis.name,
        "kind": analysis.kind,
        "price": round(candles[-1].c, 6) if candles else None,
        "change_1d": _change_pct(candles, 1),
        "change_30d": _change_pct(candles, 30),
        "change_90d": _change_pct(candles, 90),
        "signal": s.get("signal"),
        "bias": s.get("bias"),
        "score": s.get("score"),
        "conviction": s.get("conviction"),
        "blocked": s.get("blocked", False),
        "block_reason": s.get("block_reason"),
        "bars_held": s.get("bars_held", 0),
        "entry_date": s.get("entry_date"),
        "exit_note": s.get("exit_note"),
        "partial": s.get("partial"),
        "partial_done": s.get("partial_done", False),
        "rr": s.get("rr"),
        "target_banked": s.get("target_banked"),
        "adx": s.get("adx"),
        "rsi": s.get("rsi"),
        "atr_pct": s.get("atr_pct"),
        "unrealized_pct": s.get("unrealized_pct"),
        "unrealized_r": s.get("unrealized_r"),
        **levels,
        "backtest": {
            "trades": stats.get("trades"),
            "win_rate": stats.get("win_rate"),
            "expectancy_r": stats.get("expectancy_r"),
            "profit_factor": stats.get("profit_factor"),
            "total_return_pct": stats.get("total_return_pct"),
            "max_drawdown_pct": stats.get("max_drawdown_pct"),
            "sharpe": stats.get("sharpe"),
            "t_stat": stats.get("t_stat"),
            "buy_hold_pct": stats.get("buy_hold_pct"),
            "target_hit_rate": stats.get("target_hit_rate"),
        },
    }


def scan(kind: Optional[str] = None, refresh: bool = False) -> dict:
    """Signal table across the universe. Symbol failures are reported, not raised."""
    symbols = [
        s
        for s, _ in (config.STOCKS + config.CRYPTO)
        if kind in (None, "all") or config.asset_class(s) == kind
    ]

    # Prime the bar cache concurrently; analysis itself is pure CPU and fast.
    data.get_many(symbols)

    rows: list[dict] = []
    errors: dict[str, str] = {}
    for symbol in symbols:
        try:
            rows.append(row(analyze(symbol, refresh=refresh)))
        except data.DataError as exc:
            errors[symbol] = str(exc)
        except Exception as exc:  # noqa: BLE001 - one bad symbol must not sink the page
            errors[symbol] = f"{type(exc).__name__}: {exc}"

    rows.sort(key=lambda r: (r["signal"] != "LONG", r["signal"] != "SHORT", -abs(r["score"] or 0)))
    return {
        "rows": rows,
        "errors": errors,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "counts": {
            "long": sum(1 for r in rows if r["signal"] == "LONG"),
            "short": sum(1 for r in rows if r["signal"] == "SHORT"),
            "flat": sum(1 for r in rows if r["signal"] == "FLAT"),
            "total": len(rows),
        },
    }


def detail(symbol: str, bars: int = 260) -> dict:
    """Chart series, signal history and full backtest for one symbol."""
    analysis = analyze(symbol)
    candles = analysis.candles
    points = analysis.points
    cut = max(0, len(candles) - bars)

    window = candles[cut:]
    closes = [c.c for c in candles]
    highs = [c.h for c in candles]
    lows = [c.l for c in candles]

    ema20 = indicators.ema(closes, config.EMA_FAST)
    ema50 = indicators.ema(closes, config.EMA_SLOW)
    ema200 = indicators.ema(closes, config.EMA_TREND)

    marks: list[dict] = []
    previous = 0
    for point in points:
        if point.i < cut:
            previous = point.position
            continue
        if point.position != previous:
            if point.position != 0:
                marks.append({"t": point.t, "price": point.close, "side": point.position, "type": "entry"})
            else:
                marks.append({"t": point.t, "price": point.close, "side": previous, "type": "exit"})
        previous = point.position

    return {
        "symbol": analysis.symbol,
        "name": analysis.name,
        "kind": analysis.kind,
        "summary": analysis.summary,
        "candles": [
            {"t": c.t, "o": c.o, "h": c.h, "l": c.l, "c": c.c, "v": c.v} for c in window
        ],
        "ema20": [None if v is None else round(v, 6) for v in ema20[cut:]],
        "ema50": [None if v is None else round(v, 6) for v in ema50[cut:]],
        "ema200": [None if v is None else round(v, 6) for v in ema200[cut:]],
        "marks": marks,
        "score_history": [
            {"t": p.t, "score": p.score, "position": p.position} for p in points[cut:]
        ],
        "backtest": {k: v for k, v in analysis.stats.items() if k != "trade_log"},
        "trade_log": analysis.stats.get("trade_log", []),
        "universe_window": {"bars": len(candles), "start": analysis.stats.get("start"), "end": analysis.stats.get("end")},
    }


def warm() -> None:
    """Prime the cache at startup so the first page load is not a cold fetch."""
    try:
        scan()
    except Exception:  # noqa: BLE001 - warming is best-effort
        pass


# --------------------------------------------------------------------------
# trade alerts: every signal change, reconstructed from the series
# --------------------------------------------------------------------------


def _label(position: int) -> str:
    return "LONG" if position > 0 else "SHORT" if position < 0 else "FLAT"


def trades_payload() -> dict:
    """Logged trades marked against the latest close."""
    rows: list[dict] = []
    prices: dict[str, float] = {}
    now = datetime.now(timezone.utc)

    for trade in journal.list_all():
        symbol = trade["symbol"]
        if symbol not in prices:
            try:
                prices[symbol] = data.get_candles(symbol)[-1].c
            except data.DataError:
                prices[symbol] = None
        price_now = prices[symbol]

        entry = trade.get("entry")
        closed = trade["status"] == "closed"
        mark = trade.get("exit") if closed and trade.get("exit") else price_now
        side = 1 if trade["side"] == "LONG" else -1

        pnl_pct = round((mark / entry - 1) * 100 * side, 2) if entry and mark else None
        risk = abs(entry - trade["stop"]) if entry and trade.get("stop") else None
        r_multiple = round((mark - entry) * side / risk, 2) if risk and mark else None

        days_held = None
        try:
            opened = datetime.fromisoformat(trade["opened_at"])
            end = datetime.fromisoformat(trade["closed_at"]) if closed and trade.get("closed_at") else now
            days_held = round((end - opened).total_seconds() / 86400, 1)
        except (ValueError, TypeError):
            pass

        rows.append(
            {
                **trade,
                "price_now": price_now,
                "mark": mark,
                "pnl_pct": pnl_pct,
                "r_multiple": r_multiple,
                "days_held": days_held,
            }
        )

    open_rows = [row for row in rows if row["status"] == "open"]
    closed_rows = [row for row in rows if row["status"] == "closed"]
    closed_r = [row["r_multiple"] for row in closed_rows if row["r_multiple"] is not None]
    return {
        "trades": rows,
        "summary": {
            "open": len(open_rows),
            "closed": len(closed_rows),
            "open_r": round(sum(row["r_multiple"] or 0 for row in open_rows), 2),
            "closed_win_rate": round(
                sum(1 for value in closed_r if value > 0) / len(closed_r) * 100, 1
            )
            if closed_r
            else None,
        },
        # True on a serverless host: /tmp is writable but per-instance, so the
        # journal is lost when the instance is recycled.
        "storage": {
            "persistent": not config.EPHEMERAL_STORAGE,
            "path": str(config.DATA_DIR),
            "backend": journal.backend(),
            "source_env": journal.dsn_source(),
            "ignored_env": journal.ignored_env(),
        },
    }


def alerts(kind: Optional[str] = None, days: int = 30, limit: int = 300) -> dict:
    """Signal changes across the universe, newest first.

    Reconstructed from each symbol's position series, so the list has real
    history instead of starting from the first time the page was opened.
    """
    symbols = [
        s
        for s, _ in (config.STOCKS + config.CRYPTO)
        if kind in (None, "all") or config.asset_class(s) == kind
    ]
    data.get_many(symbols)

    cutoff = time.time() - days * 86400
    rows: list[dict] = []

    for symbol in symbols:
        try:
            analysis = analyze(symbol)
        except data.DataError:
            continue
        points = analysis.points
        if not points:
            continue

        last_close = analysis.candles[-1].c
        last_date = datetime.fromtimestamp(analysis.candles[-1].t, tz=timezone.utc).date()
        previous = 0
        for point in points:
            if point.position == previous:
                continue
            if point.t >= cutoff:
                if previous and point.position:
                    change_type = "flip"
                elif point.position:
                    change_type = "entry"
                else:
                    change_type = "exit"
                alert_date = datetime.fromtimestamp(point.t, tz=timezone.utc).date()
                rows.append(
                    {
                        "symbol": symbol,
                        "name": config.display_name(symbol),
                        "kind": config.asset_class(symbol),
                        "from": _label(previous),
                        "to": _label(point.position),
                        "type": change_type,
                        "t": point.t,
                        "date": datetime.fromtimestamp(point.t, tz=timezone.utc).strftime("%Y-%m-%d"),
                        "bars_ago": len(points) - 1 - point.i,
                        # Calendar days, so stocks and crypto are comparable: a
                        # crypto bar exists on weekends, a stock bar does not.
                        "days_ago": (last_date - alert_date).days,
                        "price": round(point.close, 6),
                        "price_now": round(last_close, 6),
                        "move_since": round((last_close / point.close - 1) * 100, 2) if point.close else None,
                        "score": point.score,
                        "score_now": points[-1].score,
                        "signal_now": _label(points[-1].position),
                    }
                )
            previous = point.position

    # Newest first; symbol breaks ties so the order is deterministic.
    rows.sort(key=lambda row: (-row["t"], row["symbol"]))
    return {
        "alerts": rows[:limit],
        "counts": {
            "total": len(rows),
            "flips": sum(1 for row in rows if row["type"] == "flip"),
            "entries": sum(1 for row in rows if row["type"] == "entry"),
            "exits": sum(1 for row in rows if row["type"] == "exit"),
            "window_days": days,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
