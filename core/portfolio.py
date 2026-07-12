"""Portfolio math (weights, Sharpe, drawdown, correlation, sector allocation,
diversification, risk banding, Monte Carlo simulation) and holdings CRUD."""

from __future__ import annotations

import math

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


def sector_allocation(sector_by_symbol: dict[str, str | None], weights: dict[str, float]) -> dict[str, float]:
    """Portfolio weight grouped by sector; symbols with no known sector group as 'Unknown'."""
    allocation: dict[str, float] = {}
    for symbol, weight in weights.items():
        sector = sector_by_symbol.get(symbol) or "Unknown"
        allocation[sector] = allocation.get(sector, 0.0) + weight
    return allocation


def diversification_score(weights: dict[str, float]) -> float:
    """0-100 score from the Herfindahl-Hirschman Index of position weights.

    100 means value is spread evenly across many positions; 0 means it's concentrated
    entirely in one. HHI is the sum of squared weights (1.0 for a single full position,
    1/n for n equally-weighted positions), so `(1 - HHI) * 100` rises toward 100 as the
    portfolio spreads out and falls toward 0 as it concentrates.
    """
    values = [w for w in weights.values() if w > 0]
    if not values:
        return 0.0
    hhi = sum(w**2 for w in values)
    return float(round((1 - hhi) * 100, 1))


def portfolio_volatility(daily_returns: pd.Series, annualize: bool = True) -> float:
    """Standard deviation of portfolio daily returns, annualized by default."""
    daily_returns = daily_returns.dropna()
    if daily_returns.empty:
        return 0.0
    vol = float(daily_returns.std())
    return vol * math.sqrt(TRADING_DAYS_PER_YEAR) if annualize else vol


def risk_level(volatility_annualized: float) -> str:
    """Coarse Low/Medium/High risk band from annualized volatility.

    Thresholds are calibrated to typical NSE large/mid-cap equity portfolios, which
    historically run roughly 15-35% annualized volatility.
    """
    if volatility_annualized < 0.15:
        return "Low"
    if volatility_annualized < 0.30:
        return "Medium"
    return "High"


def monte_carlo_simulation(
    daily_returns: pd.Series,
    initial_value: float,
    horizon_days: int = 252,
    num_simulations: int = 500,
    seed: int | None = 42,
) -> pd.DataFrame:
    """Simulate `num_simulations` future portfolio value paths over `horizon_days` trading
    days by bootstrap-resampling (with replacement) from the portfolio's own historical
    daily returns -- never an assumed or fabricated distribution.

    Returns a DataFrame of shape (horizon_days + 1, num_simulations); row 0 is
    `initial_value` for every column. Empty if there's no return history or a
    non-positive starting value.
    """
    returns = daily_returns.dropna().to_numpy()
    if returns.size == 0 or initial_value <= 0:
        return pd.DataFrame()
    rng = np.random.default_rng(seed)
    sampled = rng.choice(returns, size=(horizon_days, num_simulations), replace=True)
    growth = np.cumprod(1 + sampled, axis=0)
    paths = np.vstack([np.ones((1, num_simulations)), growth]) * initial_value
    return pd.DataFrame(paths)


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
