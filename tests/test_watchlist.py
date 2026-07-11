"""Integration tests for core.watchlist's DB-backed CRUD, against a throwaway DB."""

import pandas as pd
import pytest

from core.watchlist import (
    add_to_watchlist,
    is_in_watchlist,
    list_watchlist,
    remove_from_watchlist,
    seed_default_watchlist_if_empty,
)


def _fake_history() -> pd.DataFrame:
    return pd.DataFrame(
        {"Open": [100.0], "High": [101.0], "Low": [99.0], "Close": [100.5], "Volume": [1_000_000]},
        index=pd.to_datetime(["2024-01-01"]),
    )


@pytest.fixture(autouse=True)
def _mock_yfinance(monkeypatch):
    monkeypatch.setattr(
        "core.data_ingestion.yf.Ticker",
        lambda symbol: type(
            "T",
            (),
            {
                "info": {"shortName": "Reliance Industries Ltd.", "sector": "Energy"},
                "history": lambda self, **kwargs: _fake_history(),
            },
        )(),
    )


def test_add_and_list_watchlist(temp_db):
    added, message = add_to_watchlist("reliance.ns")

    assert added is True
    assert "RELIANCE.NS" in message
    entries = list_watchlist()
    assert len(entries) == 1
    assert entries[0]["symbol"] == "RELIANCE.NS"


def test_add_by_company_name_resolves_to_canonical_symbol(temp_db):
    added, _ = add_to_watchlist("reliance")

    assert added is True
    assert list_watchlist()[0]["symbol"] == "RELIANCE.NS"


def test_adding_duplicate_is_a_graceful_noop_not_an_error(temp_db):
    add_to_watchlist("RELIANCE.NS")
    added_again, message = add_to_watchlist("RELIANCE.NS")

    assert added_again is False
    assert "already" in message.lower()
    assert len(list_watchlist()) == 1


def test_is_in_watchlist(temp_db):
    assert is_in_watchlist("RELIANCE.NS") is False
    add_to_watchlist("RELIANCE.NS")
    assert is_in_watchlist("RELIANCE.NS") is True


def test_remove_from_watchlist(temp_db):
    add_to_watchlist("RELIANCE.NS")
    remove_from_watchlist("RELIANCE.NS")

    assert list_watchlist() == []
    assert is_in_watchlist("RELIANCE.NS") is False


def test_remove_nonexistent_is_a_noop(temp_db):
    remove_from_watchlist("RELIANCE.NS")  # should not raise
    assert list_watchlist() == []


def test_seed_default_watchlist_only_runs_on_empty_watchlist(temp_db):
    seed_default_watchlist_if_empty()
    seeded = list_watchlist()
    assert len(seeded) > 0

    remove_from_watchlist(seeded[0]["symbol"])
    seed_default_watchlist_if_empty()  # should NOT re-add the removed one

    assert len(list_watchlist()) == len(seeded) - 1
