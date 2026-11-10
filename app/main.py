"""Signal Desk: long/short signal dashboard for stocks and crypto.

Run with:  python -m uvicorn app.main:app --reload
"""

from __future__ import annotations

import os
import re
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, data, journal, service

DB_ENV_PATTERN = re.compile(r"(DATABASE|POSTGRES|PRISMA|^PG|DSN|STORAGE|_DB)", re.IGNORECASE)


def _db_env_names() -> list[str]:
    """Names of env vars that look database-related. Names only, never values."""
    return sorted(name for name in os.environ if DB_ENV_PATTERN.search(name))[:20]


class TradeIn(BaseModel):
    symbol: str
    side: str  # LONG or SHORT
    entry: float | None = None
    stop: float | None = None
    target: float | None = None
    note: str | None = None
    source: str | None = None


class CloseIn(BaseModel):
    price: float | None = None

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Prime the cache in the background so the first page load is not a cold fetch.
    # On a serverless host every instance would pay for it and hold the function
    # open, so warming is local-only.
    if not os.environ.get("VERCEL"):
        threading.Thread(target=service.warm, daemon=True).start()
    yield


app = FastAPI(
    title="Signal Desk",
    description="Long/short signals for stocks and crypto, with backtested stats.",
    version="1.0.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health(db: int = Query(0, description="1 runs a live query against the journal database")) -> dict:
    payload: dict = {
        "status": "ok",
        "universe": len(config.all_symbols()),
        # Names only, never values: enough to diagnose "why is the journal on
        # SQLite" from outside without exposing a credential.
        "database": {
            "backend": journal.backend(),
            "source_env": journal.dsn_source(),
            "ignored_env": journal.ignored_env(),
            "present": _db_env_names(),
        },
        "vercel_env": os.environ.get("VERCEL_ENV"),
    }
    if db:
        # Opt-in probe: run one real query and report what came back. Without
        # this, a broken connection surfaces only as a 500 on the trades route.
        try:
            payload["database"]["probe"] = f"ok ({len(journal.list_all())} rows)"
        except Exception as exc:  # noqa: BLE001 - the point is to report it
            payload["database"]["probe"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    return payload


@app.get("/api/universe")
def universe() -> dict:
    return {
        "stocks": [{"symbol": s, "name": n} for s, n in config.STOCKS],
        "crypto": [{"symbol": s, "name": n} for s, n in config.CRYPTO],
    }


@app.get("/api/signals")
def api_signals(
    asset_class: str = Query("all", pattern="^(all|stock|crypto)$"),
    refresh: bool = False,
) -> dict:
    return service.scan(kind=asset_class, refresh=refresh)


@app.get("/api/asset/{symbol}")
def api_asset(symbol: str, bars: int = Query(260, ge=60, le=1000)) -> dict:
    symbol = symbol.upper()
    if symbol not in config.ASSET_CLASS:
        raise HTTPException(status_code=404, detail=f"{symbol} is not in the universe")
    try:
        return service.detail(symbol, bars=bars)
    except data.DataError as exc:
        raise HTTPException(status_code=502, detail=f"data unavailable: {exc}") from exc


@app.get("/api/alerts")
def api_alerts(
    asset_class: str = Query("all", pattern="^(all|stock|crypto)$"),
    days: int = Query(30, ge=1, le=365),
) -> dict:
    """Every signal change inside the window, newest first."""
    return service.alerts(kind=asset_class, days=days)


@app.get("/api/trades")
def api_trades() -> dict:
    """The journal, marked against the latest close."""
    return service.trades_payload()


@app.post("/api/trades", status_code=201)
def api_add_trade(payload: TradeIn) -> dict:
    symbol = payload.symbol.upper()
    if symbol not in config.ASSET_CLASS:
        raise HTTPException(status_code=404, detail=f"{symbol} is not in the universe")

    side = payload.side.upper()
    if side not in ("LONG", "SHORT"):
        raise HTTPException(status_code=400, detail="side must be LONG or SHORT")

    entry = payload.entry
    if entry is None:
        try:
            entry = data.get_candles(symbol)[-1].c
        except data.DataError:
            entry = None

    trade = journal.add(
        symbol=symbol,
        side=1 if side == "LONG" else -1,
        entry=entry,
        stop=payload.stop,
        target=payload.target,
        note=payload.note,
        source=payload.source,
    )
    return trade


@app.post("/api/trades/{trade_id}/close")
def api_close_trade(trade_id: int, payload: CloseIn | None = None) -> dict:
    price = payload.price if payload else None
    if price is None:
        trade = journal.get(trade_id)
        if not trade:
            raise HTTPException(status_code=404, detail="trade not found")
        try:
            price = data.get_candles(trade["symbol"])[-1].c
        except data.DataError:
            price = None
    closed = journal.close(trade_id, price)
    if not closed:
        raise HTTPException(status_code=404, detail="trade not found")
    return closed


@app.delete("/api/trades/{trade_id}")
def api_delete_trade(trade_id: int) -> dict:
    if not journal.delete(trade_id):
        raise HTTPException(status_code=404, detail="trade not found")
    return {"deleted": trade_id}


@app.get("/api/backtest/{symbol}")
def api_backtest(symbol: str) -> dict:
    symbol = symbol.upper()
    if symbol not in config.ASSET_CLASS:
        raise HTTPException(status_code=404, detail=f"{symbol} is not in the universe")
    try:
        return service.analyze(symbol).stats
    except data.DataError as exc:
        raise HTTPException(status_code=502, detail=f"data unavailable: {exc}") from exc
