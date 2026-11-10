"""Verify the Postgres journal backend against a real database.

    $env:DATABASE_URL = "postgresql://user:pass@host/db?sslmode=require"
    python scripts/check_postgres.py

Runs the whole surface the API uses — schema, insert, idempotency, list, close,
delete — against whatever DATABASE_URL points at, then cleans up after itself.
Written because the Postgres path could not be exercised locally: there was no
running server to point it at.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import journal  # noqa: E402

SOURCE = "smoke-test:AAPL:LONG"


def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is not set. Point it at a Postgres database first.", file=sys.stderr)
        return 2

    print(f"backend       : {journal.backend()}")
    print(f"host          : {dsn.split('@')[-1].split('/')[0]}")

    journal.init()
    print("schema        : ok")

    # Start clean so a re-run is not confused by the previous one.
    for existing in journal.list_all():
        if existing.get("source") == SOURCE:
            journal.delete(existing["id"])

    trade = journal.add("AAPL", 1, entry=200.0, stop=190.0, target=230.0, note="smoke", source=SOURCE)
    print(f"insert        : id={trade['id']} side={trade['side']} status={trade['status']}")

    again = journal.add("AAPL", 1, entry=200.0, stop=190.0, target=230.0, note="smoke", source=SOURCE)
    ok_idempotent = again["id"] == trade["id"]
    print(f"idempotent    : {'ok' if ok_idempotent else 'FAILED — duplicate row created'}")

    listed = journal.list_all()
    print(f"list          : {len(listed)} row(s)")

    closed = journal.close(trade["id"], 210.0)
    ok_close = bool(closed) and closed["status"] == "closed" and closed["exit"] == 210.0
    print(f"close         : {'ok' if ok_close else 'FAILED — ' + str(closed)}")

    deleted = journal.delete(trade["id"])
    print(f"delete        : {'ok' if deleted else 'FAILED'}")
    print(f"rows after    : {len(journal.list_all())}")

    ok = ok_idempotent and ok_close and deleted
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
