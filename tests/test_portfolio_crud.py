"""Integration tests for core.portfolio's DB-backed CRUD functions, against a throwaway DB."""

import pytest
from sqlalchemy import event

from core.portfolio import (
    add_holding,
    create_portfolio,
    delete_holding,
    delete_portfolio,
    DuplicatePortfolioNameError,
    list_holdings,
    list_portfolios,
    update_holding,
)


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


# --- update_holding (Edit Position) ---------------------------------------------------


def test_update_holding_changes_shares_and_avg_cost(temp_db):
    portfolio_id = create_portfolio("Core Holdings")
    holding_id = add_holding(portfolio_id, "RELIANCE.NS", shares=10, avg_cost=1200.0)

    result = update_holding(holding_id, shares=25, avg_cost=1350.0)

    assert result is True
    holdings = list_holdings(portfolio_id)
    assert len(holdings) == 1  # same row, not a new one
    assert holdings[0] == {"id": holding_id, "symbol": "RELIANCE.NS", "shares": 25, "avg_cost": 1350.0}


def test_update_holding_nonexistent_returns_false_not_raise(temp_db):
    assert update_holding(999999, shares=1, avg_cost=1.0) is False


def test_update_holding_rejects_non_positive_shares(temp_db):
    portfolio_id = create_portfolio("Core Holdings")
    holding_id = add_holding(portfolio_id, "RELIANCE.NS", shares=10, avg_cost=1200.0)

    with pytest.raises(ValueError):
        update_holding(holding_id, shares=0, avg_cost=1200.0)
    with pytest.raises(ValueError):
        update_holding(holding_id, shares=-5, avg_cost=1200.0)

    # rejected updates must not have partially applied
    assert list_holdings(portfolio_id)[0]["shares"] == 10


def test_update_holding_rejects_non_finite_values(temp_db):
    portfolio_id = create_portfolio("Core Holdings")
    holding_id = add_holding(portfolio_id, "RELIANCE.NS", shares=10, avg_cost=1200.0)

    with pytest.raises(ValueError):
        update_holding(holding_id, shares=float("nan"), avg_cost=1200.0)
    with pytest.raises(ValueError):
        update_holding(holding_id, shares=float("inf"), avg_cost=1200.0)
    with pytest.raises(ValueError):
        update_holding(holding_id, shares=10, avg_cost=float("nan"))

    # rejected updates must not have partially applied
    assert list_holdings(portfolio_id)[0]["shares"] == 10


def test_update_holding_supports_fractional_shares(temp_db):
    portfolio_id = create_portfolio("Core Holdings")
    holding_id = add_holding(portfolio_id, "RELIANCE.NS", shares=10, avg_cost=1200.0)

    update_holding(holding_id, shares=12.5, avg_cost=1200.0)

    assert list_holdings(portfolio_id)[0]["shares"] == 12.5


# --- add_holding validation (zero/negative quantity, negative price) -----------------


def test_add_holding_rejects_zero_shares(temp_db):
    portfolio_id = create_portfolio("Core Holdings")
    with pytest.raises(ValueError):
        add_holding(portfolio_id, "RELIANCE.NS", shares=0, avg_cost=1200.0)
    assert list_holdings(portfolio_id) == []  # no partial row left behind


def test_add_holding_rejects_negative_shares(temp_db):
    portfolio_id = create_portfolio("Core Holdings")
    with pytest.raises(ValueError):
        add_holding(portfolio_id, "RELIANCE.NS", shares=-1, avg_cost=1200.0)
    assert list_holdings(portfolio_id) == []


def test_add_holding_rejects_negative_avg_cost(temp_db):
    portfolio_id = create_portfolio("Core Holdings")
    with pytest.raises(ValueError):
        add_holding(portfolio_id, "RELIANCE.NS", shares=10, avg_cost=-1.0)
    assert list_holdings(portfolio_id) == []


def test_add_holding_supports_fractional_shares(temp_db):
    portfolio_id = create_portfolio("Core Holdings")
    add_holding(portfolio_id, "RELIANCE.NS", shares=2.5, avg_cost=1200.0)
    assert list_holdings(portfolio_id)[0]["shares"] == 2.5


def test_add_holding_rejects_nan_shares(temp_db):
    portfolio_id = create_portfolio("Core Holdings")
    with pytest.raises(ValueError):
        add_holding(portfolio_id, "RELIANCE.NS", shares=float("nan"), avg_cost=1200.0)
    assert list_holdings(portfolio_id) == []


def test_add_holding_rejects_infinite_shares(temp_db):
    portfolio_id = create_portfolio("Core Holdings")
    with pytest.raises(ValueError):
        add_holding(portfolio_id, "RELIANCE.NS", shares=float("inf"), avg_cost=1200.0)
    assert list_holdings(portfolio_id) == []


def test_add_holding_rejects_nan_avg_cost(temp_db):
    portfolio_id = create_portfolio("Core Holdings")
    with pytest.raises(ValueError):
        add_holding(portfolio_id, "RELIANCE.NS", shares=10, avg_cost=float("nan"))
    assert list_holdings(portfolio_id) == []


def test_add_holding_rejects_infinite_avg_cost(temp_db):
    portfolio_id = create_portfolio("Core Holdings")
    with pytest.raises(ValueError):
        add_holding(portfolio_id, "RELIANCE.NS", shares=10, avg_cost=float("inf"))
    assert list_holdings(portfolio_id) == []


# --- create_portfolio uniqueness + timestamps ----------------------------------------


def test_create_portfolio_rejects_case_insensitive_duplicate_name(temp_db):
    create_portfolio("Core Holdings")
    with pytest.raises(DuplicatePortfolioNameError):
        create_portfolio("core holdings")
    with pytest.raises(DuplicatePortfolioNameError):
        create_portfolio("CORE HOLDINGS")


def test_create_portfolio_duplicate_rejection_does_not_create_a_second_row(temp_db):
    create_portfolio("Core Holdings")
    try:
        create_portfolio("Core Holdings")
    except DuplicatePortfolioNameError:
        pass
    assert len([p for p in list_portfolios() if p["name"] == "Core Holdings"]) == 1


def test_create_portfolio_allows_different_names(temp_db):
    id_a = create_portfolio("Long Term")
    id_b = create_portfolio("Swing Trading")
    assert id_a != id_b
    names = {p["name"] for p in list_portfolios()}
    assert {"Long Term", "Swing Trading"} <= names


def test_list_portfolios_includes_timestamps(temp_db):
    create_portfolio("Core Holdings")
    portfolio = list_portfolios()[0]
    assert "created_at" in portfolio
    assert "updated_at" in portfolio
    assert portfolio["created_at"] is not None


def test_add_holding_bumps_portfolio_updated_at(temp_db):
    portfolio_id = create_portfolio("Core Holdings")
    before = next(p for p in list_portfolios() if p["id"] == portfolio_id)
    assert before["updated_at"] is None  # never touched yet

    add_holding(portfolio_id, "RELIANCE.NS", shares=10, avg_cost=1200.0)

    after = next(p for p in list_portfolios() if p["id"] == portfolio_id)
    assert after["updated_at"] is not None


# --- delete_portfolio (cascades holdings) --------------------------------------------


def test_delete_portfolio_removes_portfolio_and_its_holdings(temp_db):
    portfolio_id = create_portfolio("Swing Trading")
    add_holding(portfolio_id, "RELIANCE.NS", shares=10, avg_cost=1200.0)
    add_holding(portfolio_id, "TCS.NS", shares=5, avg_cost=2000.0)

    result = delete_portfolio(portfolio_id)

    assert result is True
    assert portfolio_id not in {p["id"] for p in list_portfolios()}
    assert list_holdings(portfolio_id) == []  # no orphaned holdings


def test_delete_portfolio_does_not_affect_other_portfolios(temp_db):
    keep_id = create_portfolio("Long Term")
    add_holding(keep_id, "RELIANCE.NS", shares=10, avg_cost=1200.0)
    delete_id = create_portfolio("Swing Trading")
    add_holding(delete_id, "TCS.NS", shares=5, avg_cost=2000.0)

    delete_portfolio(delete_id)

    assert keep_id in {p["id"] for p in list_portfolios()}
    assert len(list_holdings(keep_id)) == 1


def test_delete_portfolio_nonexistent_returns_false_not_raise(temp_db):
    assert delete_portfolio(999999) is False


# --- empty portfolio edge case --------------------------------------------------------


def test_empty_portfolio_has_no_holdings(temp_db):
    portfolio_id = create_portfolio("Fresh Portfolio")
    assert list_holdings(portfolio_id) == []


# --- N+1 query regression (Production Stabilization Phase 3/4 finding) ---------------


def test_list_holdings_does_not_n_plus_one(temp_db):
    """Regression test for a real N+1 query bug found via live profiling: `list_holdings`
    used to lazy-load each row's `ticker` relationship one query at a time (1 query for
    the holdings + N queries for their tickers). Measured live at 5,000 rows: 1,880ms
    before the fix, ~620ms after (eager-loading via `selectinload`). This test asserts
    the query *count* stays flat as row count grows, not just that it's "fast enough"
    (a timing assertion would be flaky in CI) -- 20 holdings must not take meaningfully
    more queries than 2 (holdings query + ticker query), which is exactly the invariant
    `selectinload` provides regardless of row count."""
    from core.database import SessionLocal

    portfolio_id = create_portfolio("N+1 Regression Test")
    for i in range(20):
        add_holding(portfolio_id, f"NPLUS1TEST{i}.NS", shares=1, avg_cost=100.0)

    engine = SessionLocal.kw["bind"]
    queries = []

    def _count(conn, cursor, statement, parameters, context, executemany):
        if statement.strip().upper().startswith("SELECT"):
            queries.append(statement)

    event.listen(engine, "before_cursor_execute", _count)
    try:
        holdings = list_holdings(portfolio_id)
    finally:
        event.remove(engine, "before_cursor_execute", _count)

    assert len(holdings) == 20
    assert len(queries) <= 3, f"expected at most ~2-3 SELECTs (batched), got {len(queries)} -- N+1 has regressed"
