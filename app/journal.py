"""Trade journal: the trades you actually took.

Two backends behind one interface:

* **SQLite** (default) — a file at ``data/trades.db``, used locally and on any
  host with a real disk.
* **Postgres** — used automatically when ``DATABASE_URL`` is set, which is what
  a serverless host needs: Vercel's filesystem is read-only apart from ``/tmp``,
  and ``/tmp`` does not survive an instance being recycled.

Kept separate from the market-data cache, because clearing cached bars must
never delete your book.

A trade is idempotent on ``source``: clicking "I took it" twice on the same alert
returns the existing record instead of opening a second position.

Timestamps are stored as ISO-8601 TEXT in both backends. That keeps the two
implementations identical in behaviour and avoids silently mixing naive and
aware datetimes on the way out.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Protocol

from . import config

DB_PATH = config.DATA_DIR / "trades.db"

COLUMNS = "symbol, side, opened_at, entry, stop, target, note, source"

SCHEMA_SQLITE = """
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

SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS trades (
    id        BIGSERIAL PRIMARY KEY,
    symbol    TEXT    NOT NULL,
    side      SMALLINT NOT NULL,             -- 1 long, -1 short
    opened_at TEXT    NOT NULL,
    entry     DOUBLE PRECISION,
    stop      DOUBLE PRECISION,
    target    DOUBLE PRECISION,
    status    TEXT    NOT NULL DEFAULT 'open',
    closed_at TEXT,
    exit      DOUBLE PRECISION,
    note      TEXT,
    source    TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS trades_status ON trades (status);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row(row) -> dict:
    data = dict(row)
    data["side"] = "LONG" if data["side"] > 0 else "SHORT"
    return data


class Store(Protocol):
    def init(self) -> None: ...
    def add(self, symbol, side, entry, stop, target, note, source) -> dict: ...
    def list_all(self, status: Optional[str]) -> list[dict]: ...
    def get(self, trade_id: int) -> Optional[dict]: ...
    def close(self, trade_id: int, price: Optional[float]) -> Optional[dict]: ...
    def delete(self, trade_id: int) -> bool: ...


class SqliteStore:
    def __init__(self, path: Path):
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def init(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA_SQLITE)

    def add(self, symbol, side, entry, stop, target, note, source) -> dict:
        self.init()
        with self._connect() as conn:
            cursor = conn.execute(
                f"INSERT OR IGNORE INTO trades ({COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (symbol, 1 if side > 0 else -1, _now(), entry, stop, target, note, source),
            )
            conn.commit()
            if cursor.rowcount == 0 and source:
                existing = conn.execute("SELECT * FROM trades WHERE source = ?", (source,)).fetchone()
                if existing:
                    return _row(existing)
            row = conn.execute("SELECT * FROM trades WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return _row(row)

    def list_all(self, status: Optional[str] = None) -> list[dict]:
        self.init()
        query = "SELECT * FROM trades"
        params: tuple = ()
        if status in ("open", "closed"):
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY opened_at DESC, id DESC"
        with self._connect() as conn:
            return [_row(row) for row in conn.execute(query, params).fetchall()]

    def get(self, trade_id: int) -> Optional[dict]:
        self.init()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
        return _row(row) if row else None

    def close(self, trade_id: int, price: Optional[float] = None) -> Optional[dict]:
        self.init()
        with self._connect() as conn:
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

    def delete(self, trade_id: int) -> bool:
        self.init()
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM trades WHERE id = ?", (trade_id,))
            conn.commit()
            return cursor.rowcount > 0


class PostgresStore:
    """Postgres backend. Imported lazily so a local SQLite run needs no driver."""

    def __init__(self, dsn: str):
        self.dsn = dsn

    def _connect(self):
        import psycopg

        return psycopg.connect(self.dsn, connect_timeout=10)

    def init(self) -> None:
        with self._connect() as conn:
            conn.execute(SCHEMA_PG)

    def add(self, symbol, side, entry, stop, target, note, source) -> dict:
        from psycopg.rows import dict_row

        self.init()
        with self._connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"INSERT INTO trades ({COLUMNS}) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (source) DO NOTHING RETURNING *",
                (symbol, 1 if side > 0 else -1, _now(), entry, stop, target, note, source),
            )
            row = cur.fetchone()
            if row is None and source:
                cur.execute("SELECT * FROM trades WHERE source = %s", (source,))
                row = cur.fetchone()
        return _row(row)

    def list_all(self, status: Optional[str] = None) -> list[dict]:
        from psycopg.rows import dict_row

        self.init()
        query = "SELECT * FROM trades"
        params: tuple = ()
        if status in ("open", "closed"):
            query += " WHERE status = %s"
            params = (status,)
        query += " ORDER BY opened_at DESC, id DESC"
        with self._connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, params)
            return [_row(row) for row in cur.fetchall()]

    def get(self, trade_id: int) -> Optional[dict]:
        from psycopg.rows import dict_row

        self.init()
        with self._connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM trades WHERE id = %s", (trade_id,))
            row = cur.fetchone()
        return _row(row) if row else None

    def close(self, trade_id: int, price: Optional[float] = None) -> Optional[dict]:
        from psycopg.rows import dict_row

        self.init()
        with self._connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM trades WHERE id = %s", (trade_id,))
            if cur.fetchone() is None:
                return None
            cur.execute(
                "UPDATE trades SET status = 'closed', closed_at = %s, exit = %s WHERE id = %s",
                (_now(), price, trade_id),
            )
            cur.execute("SELECT * FROM trades WHERE id = %s", (trade_id,))
            row = cur.fetchone()
        return _row(row) if row else None

    def delete(self, trade_id: int) -> bool:
        self.init()
        with self._connect() as conn:
            result = conn.execute("DELETE FROM trades WHERE id = %s", (trade_id,))
            return result.rowcount > 0


_STORES: dict[tuple[str, str], Store] = {}


# Env vars a host might use for the database, in preference order. Prisma
# Postgres exposes several at once, and only some speak the Postgres wire
# protocol -- ``prisma+postgres://`` URLs are for Prisma Client, not libpq.
CANDIDATES = ("DATABASE_URL_POOLED", "POSTGRES_URL", "DATABASE_URL")
PG_SCHEMES = ("postgres://", "postgresql://")


def _clean(value: Optional[str]) -> Optional[str]:
    """Normalise a pasted DSN.

    Environment variables copied out of a .env file routinely arrive with a
    trailing newline or wrapped in quotes. Both break libpq in ways that look
    like a server problem (``invalid sslmode value: "require\\n"``), so strip
    them here rather than asking whoever pasted it to notice.
    """
    if not value:
        return None
    cleaned = value.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in ("'", '"'):
        cleaned = cleaned[1:-1].strip()
    return cleaned or None


def dsn() -> Optional[str]:
    """The Postgres URL, wherever the host decided to put it.

    Only ``postgres://`` / ``postgresql://`` strings qualify. A
    ``prisma+postgres://`` URL is the Prisma Client protocol and psycopg will
    reject it, so picking it blindly would break every journal call.
    """
    for name in CANDIDATES:
        value = _clean(os.environ.get(name))
        if value and value.startswith(PG_SCHEMES):
            return value
    return None


def dsn_source() -> Optional[str]:
    """Name of the variable that supplied the URL, for diagnostics."""
    for name in CANDIDATES:
        value = _clean(os.environ.get(name))
        if value and value.startswith(PG_SCHEMES):
            return name
    return None


def ignored_env() -> list[str]:
    """Database variables that are set but unusable as a libpq URL."""
    ignored = []
    for name in CANDIDATES:
        value = _clean(os.environ.get(name))
        if value and not value.startswith(PG_SCHEMES):
            ignored.append(name)
    return ignored


def backend() -> str:
    return "postgres" if dsn() else "sqlite"


def _store() -> Store:
    """Cached per (backend, location), so a test can repoint DB_PATH."""
    url = dsn()
    key = ("postgres", url) if url else ("sqlite", str(DB_PATH))
    if key not in _STORES:
        _STORES[key] = PostgresStore(url) if url else SqliteStore(Path(DB_PATH))
    return _STORES[key]


def init() -> None:
    _store().init()


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
    return _store().add(symbol, side, entry, stop, target, note, source)


def list_all(status: Optional[str] = None) -> list[dict]:
    return _store().list_all(status)


def get(trade_id: int) -> Optional[dict]:
    return _store().get(trade_id)


def close(trade_id: int, price: Optional[float] = None) -> Optional[dict]:
    return _store().close(trade_id, price)


def delete(trade_id: int) -> bool:
    return _store().delete(trade_id)
