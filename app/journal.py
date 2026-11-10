"""Trade journal: the trades you actually took.

Kept in its own SQLite file rather than the market-data cache, because clearing
cached bars must never delete your book.

A trade is idempotent on ``source``: clicking "I took it" twice on the same alert
returns the existing record instead of opening a second position.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import config

DB_PATH = config.DATA_DIR / "trades.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol    TEXT    NOT NULL,
    side      INTEGER NOT NULL,              -- 1 long, -1 short
    opened_at TEXT    NOT NULL,
    entry     REAL,
    stop      REAL,
    target    REAL,
    status    TEXT    NOT NULL DEFAULT 'open',
    closed_at TEXT,
    exit      REAL,
    note      TEXT,
    source    TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS trades_status ON trades (status);
"""


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def init() -> None:
    with _connect() as conn:
        conn.executescript(SCHEMA)


def _row(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["side"] = "LONG" if data["side"] > 0 else "SHORT"
    return data


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def add(
    symbol: str,
    side: int,
    entry: Optional[float] = None,
    stop: Optional[float] = None,
    target: Optional[float] = None,
    note: Optional[str] = None,
    source: Optional[str] = None,
) -> dict:
    """Record a trade. Same ``source`` twice returns the first record."""
    init()
    with _connect() as conn:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO trades (symbol, side, opened_at, entry, stop, target, note, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (symbol, 1 if side > 0 else -1, _now(), entry, stop, target, note, source),
        )
        conn.commit()
        if cursor.rowcount == 0 and source:
            existing = conn.execute("SELECT * FROM trades WHERE source = ?", (source,)).fetchone()
            if existing:
                return _row(existing)
        row = conn.execute("SELECT * FROM trades WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return _row(row)


def list_all(status: Optional[str] = None) -> list[dict]:
    init()
    query = "SELECT * FROM trades"
    params: tuple = ()
    if status in ("open", "closed"):
        query += " WHERE status = ?"
        params = (status,)
    query += " ORDER BY opened_at DESC, id DESC"
    with _connect() as conn:
        return [_row(row) for row in conn.execute(query, params).fetchall()]


def get(trade_id: int) -> Optional[dict]:
    init()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    return _row(row) if row else None


def close(trade_id: int, price: Optional[float] = None) -> Optional[dict]:
    init()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE trades SET status = 'closed', closed_at = ?, exit = ? WHERE id = ?",
            (_now(), price, trade_id),
        )
        conn.commit()
        updated = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    return _row(updated)


def delete(trade_id: int) -> bool:
    init()
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM trades WHERE id = ?", (trade_id,))
        conn.commit()
        return cursor.rowcount > 0
