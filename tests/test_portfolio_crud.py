"""Integration tests for core.portfolio's DB-backed CRUD functions, against a throwaway DB."""

import pytest

from core.portfolio import add_holding, create_portfolio, delete_holding, list_holdings, list_portfolios


@pytest.fixture(autouse=True)
def _mock_yfinance_metadata(monkeypatch):
    monkeypatch.setattr(
        "core.data_ingestion.yf.Ticker",
        lambda symbol: type("T", (), {"info": {"shortName": "Reliance Industries Ltd.", "sector": "Energy"}})(),
    )


def test_create_and_list_portfolio(temp_db):
    portfolio_id = create_portfolio("Core Holdings")

    portfolios = list_portfolios()

    assert any(p["id"] == portfolio_id and p["name"] == "Core Holdings" for p in portfolios)


def test_add_list_delete_holding(temp_db):
    portfolio_id = create_portfolio("Core Holdings")

    holding_id = add_holding(portfolio_id, "reliance.ns", shares=10, avg_cost=1200.0)
    holdings = list_holdings(portfolio_id)

    assert len(holdings) == 1
    assert holdings[0] == {"id": holding_id, "symbol": "RELIANCE.NS", "shares": 10, "avg_cost": 1200.0}

    delete_holding(holding_id)

    assert list_holdings(portfolio_id) == []


def test_add_holding_reuses_existing_ticker(temp_db):
    portfolio_id = create_portfolio("Core Holdings")

    add_holding(portfolio_id, "RELIANCE.NS", shares=5, avg_cost=1000.0)
    add_holding(portfolio_id, "RELIANCE.NS", shares=3, avg_cost=1100.0)

    holdings = list_holdings(portfolio_id)
    assert len(holdings) == 2
    assert {h["symbol"] for h in holdings} == {"RELIANCE.NS"}


def test_delete_nonexistent_holding_is_a_noop(temp_db):
    delete_holding(999999)  # should not raise
