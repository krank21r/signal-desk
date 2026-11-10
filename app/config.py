"""Configuration for Signal Desk: universe, engine parameters, cost model."""

from __future__ import annotations

import os
from pathlib import Path

# Where runtime data lives. Serverless hosts give a read-only tree with only
# /tmp writable, so this is overridable and defaults to ./data for local runs.
def _default_data_dir() -> Path:
    override = os.environ.get("SIGNAL_DESK_DATA_DIR")
    if override:
        return Path(override)
    if os.environ.get("VERCEL"):
        # Writable, but per-instance: the journal does not survive a cold start.
        return Path("/tmp/signal-desk")
    return Path(__file__).resolve().parent.parent / "data"


DATA_DIR = _default_data_dir()
EPHEMERAL_STORAGE = bool(os.environ.get("VERCEL"))

# --------------------------------------------------------------------------
# Universe
# --------------------------------------------------------------------------

STOCKS: list[tuple[str, str]] = [
    ("AAPL", "Apple"),
    ("MSFT", "Microsoft"),
    ("NVDA", "NVIDIA"),
    ("AMZN", "Amazon"),
    ("GOOGL", "Alphabet"),
    ("META", "Meta Platforms"),
    ("TSLA", "Tesla"),
    ("AMD", "Advanced Micro Devices"),
    ("JPM", "JPMorgan Chase"),
    ("XOM", "Exxon Mobil"),
    ("SPY", "S&P 500 ETF"),
    ("QQQ", "Nasdaq 100 ETF"),
]

CRYPTO: list[tuple[str, str]] = [
    ("BTC-USD", "Bitcoin"),
    ("ETH-USD", "Ethereum"),
    ("SOL-USD", "Solana"),
    ("BNB-USD", "BNB"),
    ("XRP-USD", "XRP"),
    ("DOGE-USD", "Dogecoin"),
    ("ADA-USD", "Cardano"),
    ("AVAX-USD", "Avalanche"),
]

# Binance spot pair for each crypto symbol, used as the primary feed.
BINANCE_PAIRS: dict[str, str] = {
    "BTC-USD": "BTCUSDT",
    "ETH-USD": "ETHUSDT",
    "SOL-USD": "SOLUSDT",
    "BNB-USD": "BNBUSDT",
    "XRP-USD": "XRPUSDT",
    "DOGE-USD": "DOGEUSDT",
    "ADA-USD": "ADAUSDT",
    "AVAX-USD": "AVAXUSDT",
}

ASSET_CLASS: dict[str, str] = {**{s: "stock" for s, _ in STOCKS}, **{s: "crypto" for s, _ in CRYPTO}}
NAMES: dict[str, str] = dict(STOCKS) | dict(CRYPTO)

# --------------------------------------------------------------------------
# History window (daily bars)
# --------------------------------------------------------------------------

YAHOO_RANGE = "3y"
BINANCE_LIMIT = 1000
CACHE_TTL_SECONDS = 900  # 15 minutes for bar data
ANALYSIS_TTL_SECONDS = 300  # 5 minutes for computed analysis

# --------------------------------------------------------------------------
# Indicator parameters
# --------------------------------------------------------------------------

EMA_FAST = 20
EMA_SLOW = 50
EMA_TREND = 200
RSI_PERIOD = 14
ATR_PERIOD = 14
ADX_PERIOD = 14
BREAKOUT_PERIOD = 55
MOMENTUM_PERIOD = 63
ADX_TREND_GATE = 20.0  # below this the market is too choppy for trend votes
ADX_CHOP_GATE = 15.0  # below this no new positions at all
RSI_OVERSOLD = 30.0
RSI_OVERBOUGHT = 70.0
VOL_PERCENTILE_CEILING = 0.90  # veto entries when ATR%% is in the top decile of its window
VOL_LOOKBACK = 252

# --------------------------------------------------------------------------
# Signal engine
# --------------------------------------------------------------------------

# Weights applied to each strategy vote (-1, 0, +1).
WEIGHTS: dict[str, float] = {
    "trend": 1.0,
    "momentum": 1.0,
    "breakout": 1.5,
    "mean_reversion": 1.0,
}

ENTRY_SCORE = 2.0  # |score| required to open a position
EXIT_SCORE = 0.5  # |score| below which an open position is closed (hysteresis)

STOP_ATR = 2.0
TARGET_ATR = 3.0  # 1.5R
TRAIL_ATR = 3.0  # chandelier trail distance, measured off the bar extreme
PARTIAL_FRACTION = 0.5  # share of the position taken off at the target

# How open positions end.
#   "fixed"    - initial 2xATR stop and a hard 3xATR target.
#   "trailing" - initial 2xATR stop, then trailed 3xATR behind the best price,
#                no target at all, so the right tail survives.
#   "partial"  - take half off at the 3xATR target, trail the rest 3xATR.
EXIT_MODE = "partial"

# --------------------------------------------------------------------------
# Cost model (basis points per side)
# --------------------------------------------------------------------------

COSTS_BPS: dict[str, float] = {"stock": 2.0, "crypto": 15.0}
PERIODS_PER_YEAR: dict[str, int] = {"stock": 252, "crypto": 365}

# --------------------------------------------------------------------------
# Warmup: bars needed before any signal is valid
# --------------------------------------------------------------------------

WARMUP_BARS = max(EMA_TREND, BREAKOUT_PERIOD, VOL_LOOKBACK) + ATR_PERIOD


def asset_class(symbol: str) -> str:
    return ASSET_CLASS.get(symbol, "stock")


def display_name(symbol: str) -> str:
    return NAMES.get(symbol, symbol)


def all_symbols() -> list[str]:
    return [s for s, _ in STOCKS] + [s for s, _ in CRYPTO]
