"""Market data access: Yahoo Finance (stocks + crypto fallback) and Binance spot.

No API keys required. Bars are cached in SQLite so repeated analysis, the
backtest and page reloads do not re-hit the network.
"""

from __future__ import annotations

import json
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from . import config

CACHE_DIR = config.DATA_DIR
CACHE_DB = CACHE_DIR / "cache.db"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SignalDesk/1.0"


class DataError(RuntimeError):
    """Raised when a symbol's history cannot be retrieved."""


@dataclass(frozen=True)
class Candle:
    t: int  # unix seconds, bar open
    o: float
    h: float
    l: float
    c: float
    v: float

    def as_dict(self) -> dict[str, float]:
        return {"t": self.t, "o": self.o, "h": self.h, "l": self.l, "c": self.c, "v": self.v}


# --------------------------------------------------------------------------
# cache
# --------------------------------------------------------------------------


def _connect() -> sqlite3.Connection:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(CACHE_DB, timeout=15)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS bars ("
        " key TEXT PRIMARY KEY, payload TEXT NOT NULL, fetched_at REAL NOT NULL)"
    )
    conn.commit()
    return conn


def _cache_get(key: str, ttl: int) -> Optional[list[dict]]:
    try:
        with _connect() as conn:
            row = conn.execute("SELECT payload, fetched_at FROM bars WHERE key = ?", (key,)).fetchone()
    except sqlite3.Error:
        return None
    if not row:
        return None
    payload, fetched_at = row
    if time.time() - fetched_at > ttl:
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


def _cache_put(key: str, bars: list[dict]) -> None:
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO bars (key, payload, fetched_at) VALUES (?, ?, ?)",
                (key, json.dumps(bars), time.time()),
            )
            conn.commit()
    except sqlite3.Error:
        pass  # cache is best-effort; never fail a request over it


def _cache_get_stale(key: str) -> Optional[list[dict]]:
    """Last payload regardless of age -- the offline fallback."""
    try:
        with _connect() as conn:
            row = conn.execute("SELECT payload FROM bars WHERE key = ?", (key,)).fetchone()
    except sqlite3.Error:
        return None
    if not row:
        return None
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        return None


# --------------------------------------------------------------------------
# http
# --------------------------------------------------------------------------


def _get_json(url: str, params: dict[str, str] | None = None, timeout: int = 20) -> dict:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise DataError(f"HTTP {exc.code} from {urllib.parse.urlsplit(url).netloc}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise DataError(f"request failed: {exc}") from exc


# --------------------------------------------------------------------------
# providers
# --------------------------------------------------------------------------


def fetch_yahoo(symbol: str) -> list[Candle]:
    data = _get_json(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}",
        {"range": config.YAHOO_RANGE, "interval": "1d", "includePrePost": "false"},
    )
    chart = data.get("chart") or {}
    if chart.get("error"):
        raise DataError(f"yahoo error: {chart['error']}")
    results = chart.get("result") or []
    if not results:
        raise DataError("yahoo returned no data")
    result = results[0]
    stamps: list[int] = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    opens, highs, lows, closes, volumes = (
        quote.get("open") or [],
        quote.get("high") or [],
        quote.get("low") or [],
        quote.get("close") or [],
        quote.get("volume") or [],
    )
    bars: list[Candle] = []
    for i, t in enumerate(stamps):
        try:
            o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        except IndexError:
            continue
        if None in (o, h, l, c) or c <= 0:
            continue  # holidays and halted sessions come back as nulls
        v = volumes[i] if i < len(volumes) and volumes[i] is not None else 0.0
        bars.append(Candle(int(t), float(o), float(h), float(l), float(c), float(v)))
    if not bars:
        raise DataError("yahoo returned an empty series")
    return bars


def fetch_binance(symbol: str) -> list[Candle]:
    pair = config.BINANCE_PAIRS.get(symbol)
    if not pair:
        raise DataError(f"no binance pair for {symbol}")
    rows = _get_json(
        "https://api.binance.com/api/v3/klines",
        {"symbol": pair, "interval": "1d", "limit": str(config.BINANCE_LIMIT)},
    )
    # The kline endpoint returns a bare list, not an object.
    if isinstance(rows, dict) and rows.get("code"):
        raise DataError(f"binance error: {rows.get('msg')}")
    if not isinstance(rows, list) or not rows:
        raise DataError("binance returned no data")
    bars: list[Candle] = []
    for row in rows:
        try:
            t = int(row[0]) // 1000
            o, h, l, c = (float(row[1]), float(row[2]), float(row[3]), float(row[4]))
            v = float(row[5])
        except (IndexError, TypeError, ValueError):
            continue
        if c <= 0:
            continue
        bars.append(Candle(t, o, h, l, c, v))
    if not bars:
        raise DataError("binance returned an empty series")
    return bars


# --------------------------------------------------------------------------
# public interface
# --------------------------------------------------------------------------


def get_candles(symbol: str, use_cache: bool = True) -> list[Candle]:
    """Daily bars for a symbol, newest last.

    Crypto tries Binance first and falls back to Yahoo; stocks use Yahoo.
    On network failure the last cached copy is used, so the app still renders
    offline once it has been primed.
    """
    kind = config.asset_class(symbol)
    key = f"{kind}:{symbol}"

    def _window(rows: list[Candle]) -> list[Candle]:
        """A fixed-length tail, so the history a backtest sees is the same
        whether the primary feed or a fallback answered."""
        return rows[-config.HISTORY_BARS :] if len(rows) > config.HISTORY_BARS else rows

    if use_cache:
        cached = _cache_get(key, config.CACHE_TTL_SECONDS)
        if cached:
            return _window([Candle(**row) for row in cached])

    providers = [fetch_binance, fetch_yahoo] if kind == "crypto" else [fetch_yahoo]
    errors: list[str] = []
    for provider in providers:
        try:
            bars = provider(symbol)
        except DataError as exc:
            errors.append(f"{provider.__name__}: {exc}")
            continue
        if bars:
            bars.sort(key=lambda b: b.t)
            _cache_put(key, [b.as_dict() for b in bars])
            return _window(bars)

    stale = _cache_get_stale(key)
    if stale:
        return _window([Candle(**row) for row in stale])
    raise DataError(f"no data for {symbol} ({'; '.join(errors) or 'unknown error'})")


def get_many(symbols: Iterable[str], max_workers: int = 8) -> tuple[dict[str, list[Candle]], dict[str, str]]:
    """Fetch several symbols concurrently. Returns (data, errors)."""
    from concurrent.futures import ThreadPoolExecutor

    data: dict[str, list[Candle]] = {}
    errors: dict[str, str] = {}

    def work(sym: str) -> tuple[str, list[Candle] | None, str | None]:
        try:
            return sym, get_candles(sym), None
        except DataError as exc:
            return sym, None, str(exc)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for sym, bars, err in pool.map(work, list(symbols)):
            if bars:
                data[sym] = bars
            else:
                errors[sym] = err or "unknown error"
    return data, errors


# --------------------------------------------------------------------------
# futures leverage data (crypto only) -- the direct evidence of forced closes
# --------------------------------------------------------------------------

FUTURES_TTL_SECONDS = 3600


def get_open_interest(symbol: str) -> list[dict]:
    """Daily open interest, oldest first, as [{t, v}] where v is notional value.

    A drop on the sweep bar means positions were closed, not added.
    """
    pair = config.BINANCE_PAIRS.get(symbol)
    if not pair:
        return []
    key = f"oi:{symbol}"
    cached = _cache_get(key, FUTURES_TTL_SECONDS)
    if cached:
        return cached
    try:
        rows = _get_json(
            "https://fapi.binance.com/futures/data/openInterestHist",
            {"symbol": pair, "period": "1d", "limit": "500"},
        )
    except DataError:
        return _cache_get_stale(key) or []
    if not isinstance(rows, list):
        return []
    series = [
        {"t": int(row["timestamp"]) // 1000, "v": float(row["sumOpenInterestValue"])}
        for row in rows
        if row.get("timestamp") and row.get("sumOpenInterestValue")
    ]
    _cache_put(key, series)
    return series


def get_funding_rates(symbol: str) -> list[dict]:
    """8-hour funding history, oldest first, as [{t, v}] where v is the rate."""
    pair = config.BINANCE_PAIRS.get(symbol)
    if not pair:
        return []
    key = f"funding:{symbol}"
    cached = _cache_get(key, FUTURES_TTL_SECONDS)
    if cached:
        return cached
    try:
        rows = _get_json(
            "https://fapi.binance.com/fapi/v1/fundingRate",
            {"symbol": pair, "limit": "1000"},
        )
    except DataError:
        return _cache_get_stale(key) or []
    if not isinstance(rows, list):
        return []
    series = [
        {"t": int(row["fundingTime"]) // 1000, "v": float(row["fundingRate"])}
        for row in rows
        if row.get("fundingTime") and row.get("fundingRate")
    ]
    _cache_put(key, series)
    return series
