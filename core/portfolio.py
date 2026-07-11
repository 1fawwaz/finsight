"""Portfolio math (weights, Sharpe, drawdown, correlation) and holdings CRUD."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy import select

from core.database import Holding, Portfolio, Ticker, get_session
from core.data_ingestion import get_or_create_ticker

TRADING_DAYS_PER_YEAR = 252


def position_values(shares: dict[str, float], current_prices: dict[str, float]) -> dict[str, float]:
    """Market value of each holding: shares * current price."""
    return {symbol: shares[symbol] * current_prices[symbol] for symbol in shares}


def portfolio_weights(shares: dict[str, float], current_prices: dict[str, float]) -> dict[str, float]:
    """Fraction of total portfolio market value held in each symbol."""
    values = position_values(shares, current_prices)
    total = sum(values.values())
    if total == 0:
        return {symbol: 0.0 for symbol in values}
    return {symbol: value / total for symbol, value in values.items()}


def cumulative_returns(close: pd.Series) -> pd.Series:
    """Cumulative percentage return series starting at 0.0 on the first observation."""
    return (close / close.iloc[0]) - 1.0


def portfolio_daily_returns(price_df: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    """Weighted daily return series for a portfolio, given a DataFrame of per-symbol close prices."""
    daily_returns = price_df.pct_change().dropna(how="all")
    aligned_weights = pd.Series({col: weights.get(col, 0.0) for col in daily_returns.columns})
    return (daily_returns * aligned_weights).sum(axis=1)


def sharpe_ratio(daily_returns: pd.Series, risk_free_rate: float = 0.0, annualize: bool = True) -> float:
    """Sharpe ratio of a daily return series, given an annual risk-free rate."""
    daily_returns = daily_returns.dropna()
    if daily_returns.empty or daily_returns.std() == 0:
        return 0.0
    daily_rf = risk_free_rate / TRADING_DAYS_PER_YEAR
    excess = daily_returns - daily_rf
    ratio = excess.mean() / daily_returns.std()
    if annualize:
        ratio *= np.sqrt(TRADING_DAYS_PER_YEAR)
    return float(ratio)


def max_drawdown(close: pd.Series) -> float:
    """Maximum peak-to-trough drawdown as a negative fraction, e.g. -0.25 for a 25% drawdown."""
    running_max = close.cummax()
    drawdown = (close - running_max) / running_max
    return float(drawdown.min())


def correlation_matrix(price_df: pd.DataFrame) -> pd.DataFrame:
    """Correlation matrix of daily returns across the given symbols' close price DataFrame."""
    return price_df.pct_change().dropna(how="all").corr()


# --- Holdings CRUD -----------------------------------------------------------------


def list_portfolios() -> list[dict]:
    """All portfolios as {id, name} dicts, newest first."""
    with get_session() as session:
        rows = session.execute(select(Portfolio).order_by(Portfolio.created_at.desc())).scalars().all()
        return [{"id": p.id, "name": p.name} for p in rows]


def create_portfolio(name: str) -> int:
    """Create a new empty portfolio and return its id."""
    with get_session() as session:
        portfolio = Portfolio(name=name)
        session.add(portfolio)
        session.flush()
        return portfolio.id


def list_holdings(portfolio_id: int) -> list[dict]:
    """Holdings in a portfolio as {id, symbol, shares, avg_cost} dicts."""
    with get_session() as session:
        rows = session.execute(
            select(Holding).where(Holding.portfolio_id == portfolio_id)
        ).scalars().all()
        return [
            {
                "id": h.id,
                "symbol": h.ticker.symbol,
                "shares": h.shares,
                "avg_cost": h.avg_cost,
            }
            for h in rows
        ]


def add_holding(portfolio_id: int, symbol: str, shares: float, avg_cost: float) -> int:
    """Add a holding of `shares` of `symbol` to a portfolio, creating the ticker if needed."""
    with get_session() as session:
        ticker = get_or_create_ticker(session, symbol)
        holding = Holding(portfolio_id=portfolio_id, ticker_id=ticker.id, shares=shares, avg_cost=avg_cost)
        session.add(holding)
        session.flush()
        return holding.id


def delete_holding(holding_id: int) -> None:
    """Remove a holding by id."""
    with get_session() as session:
        holding = session.get(Holding, holding_id)
        if holding is not None:
            session.delete(holding)
