"""Trade journal storage: add, list, close, delete, and idempotency."""

from __future__ import annotations

import pytest

from app import journal


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "DB_PATH", tmp_path / "trades.db")
    journal.init()


def test_add_opens_a_long_trade():
    trade = journal.add("AAPL", 1, entry=200.0, stop=190.0, target=230.0)
    assert trade["symbol"] == "AAPL"
    assert trade["side"] == "LONG"
    assert trade["status"] == "open"
    assert trade["entry"] == 200.0
    assert trade["closed_at"] is None


def test_short_side_is_stored_as_minus_one():
    trade = journal.add("BTC-USD", -1, entry=76000.0)
    assert trade["side"] == "SHORT"
    with journal._connect() as conn:  # raw value, not the display label
        raw = conn.execute("SELECT side FROM trades WHERE id = ?", (trade["id"],)).fetchone()[0]
    assert raw == -1


def test_same_source_is_idempotent():
    first = journal.add("NVDA", -1, entry=200.0, source="NVDA:2026-10-30:SHORT")
    second = journal.add("NVDA", -1, entry=200.0, source="NVDA:2026-10-30:SHORT")
    assert first["id"] == second["id"]
    assert len(journal.list_all()) == 1


def test_different_sources_create_separate_trades():
    journal.add("NVDA", -1, entry=200.0, source="NVDA:2026-10-30:SHORT")
    journal.add("NVDA", -1, entry=250.0, source="NVDA:2026-11-05:SHORT")
    assert len(journal.list_all()) == 2


def test_trades_without_source_are_never_deduplicated():
    journal.add("AAPL", 1, entry=100.0)
    journal.add("AAPL", 1, entry=100.0)
    assert len(journal.list_all()) == 2


def test_close_sets_exit_and_status():
    trade = journal.add("AAPL", 1, entry=200.0)
    closed = journal.close(trade["id"], 210.0)
    assert closed["status"] == "closed"
    assert closed["exit"] == 210.0
    assert closed["closed_at"]


def test_close_without_price_leaves_exit_empty():
    trade = journal.add("AAPL", 1, entry=200.0)
    closed = journal.close(trade["id"])
    assert closed["status"] == "closed"
    assert closed["exit"] is None


def test_list_filters_by_status():
    open_trade = journal.add("AAPL", 1, entry=200.0)
    journal.add("MSFT", 1, entry=500.0)
    journal.close(open_trade["id"], 210.0)
    assert len(journal.list_all("open")) == 1
    assert len(journal.list_all("closed")) == 1
    assert len(journal.list_all()) == 2


def test_delete_removes_the_row():
    trade = journal.add("AAPL", 1, entry=200.0)
    assert journal.delete(trade["id"]) is True
    assert journal.list_all() == []
    assert journal.delete(trade["id"]) is False


def test_close_unknown_id_returns_none():
    assert journal.close(9999, 100.0) is None
