"""Signal engine.

Four independent strategy families vote -1 / 0 / +1 on every bar. Votes are
weighted, summed into a score, and passed through a state machine with
hysteresis, so a position opens only on a decisive score and closes when the
evidence fades rather than on every tick of noise.

Everything here is causal: the score at bar ``i`` is built only from bars up to
and including ``i``. Execution happens on the next bar's open (see backtest.py),
so nothing in the pipeline can peek at the future.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

from . import config, indicators
from .data import Candle


@dataclass
class Vote:
    name: str
    weight: float
    vote: int
    label: str
    detail: str


@dataclass
class SignalPoint:
    i: int
    t: int
    close: float
    score: float
    position: int  # -1 short, 0 flat, +1 long
    bias: str
    atr: Optional[float]
    adx: Optional[float]
    rsi: Optional[float]
    votes: list[Vote] = field(default_factory=list)
    blocked: bool = False
    block_reason: Optional[str] = None
    changed: bool = False  # position differs from previous bar

    @property
    def conviction(self) -> str:
        magnitude = abs(self.score)
        if magnitude >= 3.0:
            return "high"
        if magnitude >= 2.0:
            return "medium"
        if magnitude >= 1.0:
            return "low"
        return "none"


def _bias(score: float) -> str:
    if score >= config.ENTRY_SCORE:
        return "long"
    if score >= 1.0:
        return "lean_long"
    if score <= -config.ENTRY_SCORE:
        return "short"
    if score <= -1.0:
        return "lean_short"
    return "neutral"


def levels(price: float, atr_value: float, side: int) -> dict[str, float]:
    """Stop and target for a fresh position, under the configured exit rule."""
    if side == 0 or atr_value <= 0:
        return {}
    stop = price - side * config.STOP_ATR * atr_value
    risk = abs(price - stop)
    out: dict[str, float] = {
        "stop": round(stop, 6),
        "risk_pct": round(risk / price * 100, 2),
    }
    if config.EXIT_MODE in ("fixed", "partial"):
        target = price + side * config.TARGET_ATR * atr_value
        reward = abs(target - price)
        out["target"] = round(target, 6)
        out["reward_pct"] = round(reward / price * 100, 2)
        out["rr"] = round(reward / risk, 2) if risk else 0.0
        if config.EXIT_MODE == "partial":
            # Half comes off here; the remainder rides the trail.
            out["partial"] = config.PARTIAL_FRACTION
    else:
        # No fixed target: the trade runs until the trail is hit or the
        # signal fades. Surfacing a fake target here would misrepresent it.
        out["trail_atr"] = config.TRAIL_ATR
    return out


def series(candles: Sequence[Candle]) -> list[SignalPoint]:
    """Signal for every bar. Returns an empty list when history is too short."""
    n = len(candles)
    if n < 60:
        return []

    closes = [c.c for c in candles]
    highs = [c.h for c in candles]
    lows = [c.l for c in candles]

    ema_fast = indicators.ema(closes, config.EMA_FAST)
    ema_slow = indicators.ema(closes, config.EMA_SLOW)
    ema_trend = indicators.ema(closes, config.EMA_TREND)
    adx_series, plus_di, minus_di = indicators.adx(highs, lows, closes, config.ADX_PERIOD)
    rsi_series = indicators.rsi(closes, config.RSI_PERIOD)
    atr_series = indicators.atr(highs, lows, closes, config.ATR_PERIOD)
    roc_series = indicators.roc(closes, config.MOMENTUM_PERIOD)
    donch_hi = indicators.rolling_max(highs, config.BREAKOUT_PERIOD)
    donch_lo = indicators.rolling_min(lows, config.BREAKOUT_PERIOD)

    atr_pct: list[Optional[float]] = [
        (atr_series[i] / closes[i] * 100.0) if atr_series[i] and closes[i] else None for i in range(n)
    ]
    atr_rank: list[Optional[float]] = [
        indicators.percentile_rank(atr_pct, i, config.VOL_LOOKBACK) for i in range(n)
    ]

    out: list[SignalPoint] = []
    position = 0
    previous = 0

    for i in range(n):
        price = closes[i]
        a, adx_v, rsi_v = atr_series[i], adx_series[i], rsi_series[i]
        ef, es, et = ema_fast[i], ema_slow[i], ema_trend[i]
        roc_v, hi, lo = roc_series[i], donch_hi[i], donch_lo[i]
        rank = atr_rank[i]

        # Not enough history for a full read: stay flat, no votes.
        if None in (a, adx_v, rsi_v, ef, es, et, roc_v, hi, lo):
            out.append(
                SignalPoint(i, candles[i].t, price, 0.0, 0, "neutral", a, adx_v, rsi_v, [], True, "insufficient history")
            )
            continue

        votes: list[Vote] = []

        # 1. Trend: full EMA stack alignment, gated by directional strength.
        trend_vote = 0
        if adx_v >= config.ADX_TREND_GATE:
            if price > et and ef > es:
                trend_vote = 1
            elif price < et and ef < es:
                trend_vote = -1
        votes.append(
            Vote(
                "trend",
                config.WEIGHTS["trend"],
                trend_vote,
                "Trend",
                f"ADX {adx_v:.1f}, close {'above' if price > et else 'below'} EMA200, "
                f"EMA20 {'above' if ef > es else 'below'} EMA50",
            )
        )

        # 2. Momentum: 63-day rate of change, confirmed by the 50-day EMA.
        momentum_vote = 0
        if roc_v > 0 and price > es:
            momentum_vote = 1
        elif roc_v < 0 and price < es:
            momentum_vote = -1
        votes.append(
            Vote(
                "momentum",
                config.WEIGHTS["momentum"],
                momentum_vote,
                "Momentum",
                f"63d change {roc_v:+.1f}%, price {'above' if price > es else 'below'} EMA50",
            )
        )

        # 3. Breakout: close clears the prior 55-bar structure.
        breakout_vote = 0
        if price > hi:
            breakout_vote = 1
        elif price < lo:
            breakout_vote = -1
        votes.append(
            Vote(
                "breakout",
                config.WEIGHTS["breakout"],
                breakout_vote,
                "Breakout",
                f"55d range {lo:.2f} – {hi:.2f}" + (", broke out" if breakout_vote else ""),
            )
        )

        # 4. Mean reversion: stretched RSI, but only against the primary trend.
        mr_vote = 0
        if rsi_v <= config.RSI_OVERSOLD and price > et:
            mr_vote = 1
        elif rsi_v >= config.RSI_OVERBOUGHT and price < et:
            mr_vote = -1
        votes.append(
            Vote(
                "mean_reversion",
                config.WEIGHTS["mean_reversion"],
                mr_vote,
                "Mean reversion",
                f"RSI {rsi_v:.1f} ({'oversold' if rsi_v <= 30 else 'overbought' if rsi_v >= 70 else 'mid-range'})",
            )
        )

        score = sum(v.vote * v.weight for v in votes)

        # Regime gates: no fresh entries into a volatility spike or a dead trend.
        block_reason = None
        if rank is not None and rank >= config.VOL_PERCENTILE_CEILING:
            block_reason = f"volatility spike (ATR {rank * 100:.0f}th pct)"
        elif adx_v < config.ADX_CHOP_GATE:
            block_reason = f"chop (ADX {adx_v:.1f})"
        blocked = block_reason is not None

        # State machine with hysteresis.
        if position == 0:
            if not blocked and score >= config.ENTRY_SCORE:
                position = 1
            elif not blocked and score <= -config.ENTRY_SCORE:
                position = -1
        elif position > 0:
            if score <= -config.ENTRY_SCORE and not blocked:
                position = -1  # decisive reversal
            elif score < config.EXIT_SCORE:
                position = 0
        else:
            if score >= config.ENTRY_SCORE and not blocked:
                position = 1
            elif score > -config.EXIT_SCORE:
                position = 0

        out.append(
            SignalPoint(
                i=i,
                t=candles[i].t,
                close=price,
                score=round(score, 2),
                position=position,
                bias=_bias(score),
                atr=a,
                adx=adx_v,
                rsi=rsi_v,
                votes=votes,
                blocked=blocked,
                block_reason=block_reason,
                changed=position != previous,
            )
        )
        previous = position

    return out


def summarize(points: Sequence[SignalPoint], candles: Sequence[Candle]) -> dict:
    """Current state plus the levels you would trade if you acted on it now."""
    if not points:
        return {"signal": "insufficient_data"}
    last = points[-1]
    side = last.position
    payload: dict = {
        "signal": "LONG" if side > 0 else "SHORT" if side < 0 else "FLAT",
        "bias": last.bias,
        "score": last.score,
        "conviction": last.conviction,
        "blocked": last.blocked,
        "block_reason": last.block_reason,
        "as_of": last.t,
        "price": last.close,
        "adx": round(last.adx, 1) if last.adx is not None else None,
        "rsi": round(last.rsi, 1) if last.rsi is not None else None,
        "atr_pct": round(last.atr / last.close * 100, 2) if last.atr else None,
        "votes": [
            {"name": v.name, "label": v.label, "vote": v.vote, "weight": v.weight, "detail": v.detail}
            for v in last.votes
        ],
    }

    # How long the current stance has held.
    bars_held = 0
    for point in reversed(points):
        if point.position != side:
            break
        bars_held += 1
    payload["bars_held"] = bars_held if side != 0 else 0

    if side == 0:
        # Levels a fresh entry would use. Only when there is a real directional
        # lean: a zero score has no side to trade.
        if last.atr and abs(last.score) >= 1.0:
            preview = levels(last.close, last.atr, 1 if last.score > 0 else -1)
            payload["preview"] = preview
        return payload

    # Entry reference: the bar where this stance began.
    entry_point = points[-bars_held] if bars_held <= len(points) else points[0]
    entry_price = entry_point.close
    payload["entry"] = round(entry_price, 6)
    payload["unrealized_pct"] = round((last.close / entry_price - 1) * 100 * side, 2)
    if entry_point.atr:
        payload.update(levels(entry_price, entry_point.atr, side))
        risk = abs(entry_price - payload["stop"])
        if risk:
            payload["unrealized_r"] = round(abs(last.close - entry_price) / risk * (1 if last.close >= entry_price else -1) * side, 2)
    return payload
