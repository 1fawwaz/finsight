"""Portfolio math (weights, Sharpe, drawdown, correlation, sector allocation,
diversification, risk banding, Monte Carlo simulation) and holdings CRUD."""

from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from core.config import get_logger
from core.database import Holding, Portfolio, Ticker, get_session
from core.data_ingestion import get_or_create_ticker

logger = get_logger(__name__)

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


class DuplicatePortfolioNameError(Exception):
    """Raised by `create_portfolio` when a portfolio with that name (case-insensitive)
    already exists -- portfolio names are enforced unique at the application layer
    (see `Portfolio`'s docstring in core.database for why not a DB constraint)."""


def list_portfolios() -> list[dict]:
    """All portfolios as {id, name, created_at, updated_at} dicts, newest first."""
    with get_session() as session:
        rows = session.execute(select(Portfolio).order_by(Portfolio.created_at.desc())).scalars().all()
        return [
            {"id": p.id, "name": p.name, "created_at": p.created_at, "updated_at": p.updated_at}
            for p in rows
        ]


def create_portfolio(name: str) -> int:
    """Create a new empty portfolio and return its id.

    Raises `DuplicatePortfolioNameError` if a portfolio with that name (case-
    insensitive, whitespace-trimmed) already exists -- portfolio names must be
    unique, and callers must show a clear validation error rather than silently
    creating a second portfolio with the same name.
    """
    name = name.strip()
    with get_session() as session:
        existing = session.execute(
            select(Portfolio).where(func.lower(Portfolio.name) == name.lower())
        ).scalar_one_or_none()
        if existing is not None:
            raise DuplicatePortfolioNameError(f"A portfolio named {name!r} already exists.")
        portfolio = Portfolio(name=name)
        session.add(portfolio)
        session.flush()
        logger.info("portfolio_create portfolio_id=%s name=%s", portfolio.id, name)
        return portfolio.id


def delete_portfolio(portfolio_id: int) -> bool:
    """Delete a portfolio and every holding under it (cascade via the ORM
    relationship's `cascade="all, delete-orphan"`, so no orphaned `Holding` rows can
    remain). Returns False (no-op, logged) if the portfolio no longer exists.

    There is no separate transaction/ledger table in this schema to also clean up
    (only current holdings are tracked, not a buy/sell history), and no portfolio-
    keyed calculation cache exists either -- the only caching involved
    (`@st.cache_data` on price/ticker-info lookups in pages/3_Portfolio.py) is keyed
    by symbol, not portfolio, and needs no invalidation when a portfolio is deleted.
    """
    with get_session() as session:
        try:
            portfolio = session.get(Portfolio, portfolio_id)
            if portfolio is None:
                logger.warning("portfolio_delete_not_found portfolio_id=%s", portfolio_id)
                return False
            holding_count = len(portfolio.holdings)
            name = portfolio.name
            session.delete(portfolio)
            logger.info(
                "portfolio_delete portfolio_id=%s name=%s holdings_removed=%d",
                portfolio_id, name, holding_count,
            )
            return True
        except Exception as exc:
            logger.error("portfolio_delete_failed portfolio_id=%s error=%s", portfolio_id, exc)
            raise


def _touch_portfolio(session, portfolio_id: int) -> None:
    """Bump a portfolio's `updated_at` -- called whenever a holding under it
    changes, so "last modified" reflects real portfolio activity."""
    portfolio = session.get(Portfolio, portfolio_id)
    if portfolio is not None:
        portfolio.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)


def get_all_held_symbols() -> set[str]:
    """Every symbol held in *any* portfolio -- unlike `list_holdings` (one portfolio at
    a time), this answers "is this symbol held anywhere" for the search engine's
    personalization boost, which doesn't care which portfolio."""
    with get_session() as session:
        rows = session.execute(select(Ticker.symbol).join(Holding, Holding.ticker_id == Ticker.id).distinct()).scalars().all()
        return set(rows)


def list_holdings(portfolio_id: int) -> list[dict]:
    """Holdings in a portfolio as {id, symbol, shares, avg_cost} dicts.

    Eager-loads each holding's `ticker` relationship in one extra batched query
    (`selectinload`) instead of lazy-loading it one row at a time. Measured live
    this session: a 5,000-row portfolio took 1,880ms without this (one query per
    row -- a classic N+1) versus a single-digit-millisecond batched query with it.
    """
    with get_session() as session:
        rows = session.execute(
            select(Holding).where(Holding.portfolio_id == portfolio_id).options(selectinload(Holding.ticker))
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
    """Add a holding of `shares` of `symbol` to a portfolio, creating the ticker if needed.

    Deliberately does not merge into an existing holding of the same symbol -- adding
    the same symbol again creates a second lot (own shares/avg_cost), matching how a
    brokerage statement records separate buys; this is existing, tested behavior (see
    tests/test_portfolio_crud.py::test_add_holding_reuses_existing_ticker), not
    something this fix changes. Portfolio-level totals must aggregate multiple lots of
    the same symbol via `aggregate_shares_by_symbol` below, not assume one row per
    symbol.

    Raises `ValueError` for a non-positive share count, a negative average cost, or
    a non-finite value (NaN/Infinity -- e.g. from a malformed upstream computation) --
    a server-side safety net (every existing UI call site already validates this
    itself before calling) so a future caller can't silently persist an invalid
    position by skipping the UI-level check. Non-finite values are rejected here
    explicitly rather than left to fail as an opaque `sqlite3.IntegrityError` deep in
    the DB layer (confirmed live: NaN currently trips SQLite's NOT NULL constraint
    with a raw SQL error message, and +/-Infinity was previously accepted outright,
    silently poisoning every downstream calculation that touches this holding --
    portfolio value, allocation weights, Sharpe ratio all become NaN/Infinity too).
    """
    if not math.isfinite(shares):
        raise ValueError(f"shares must be a finite number, got {shares!r}")
    if not math.isfinite(avg_cost):
        raise ValueError(f"avg_cost must be a finite number, got {avg_cost!r}")
    if shares <= 0:
        raise ValueError(f"shares must be positive, got {shares!r}")
    if avg_cost < 0:
        raise ValueError(f"avg_cost can't be negative, got {avg_cost!r}")
    with get_session() as session:
        try:
            ticker = get_or_create_ticker(session, symbol)
            holding = Holding(portfolio_id=portfolio_id, ticker_id=ticker.id, shares=shares, avg_cost=avg_cost)
            session.add(holding)
            session.flush()
            _touch_portfolio(session, portfolio_id)
            logger.info(
                "portfolio_add_holding portfolio_id=%s symbol=%s shares=%s avg_cost=%s holding_id=%s",
                portfolio_id, ticker.symbol, shares, avg_cost, holding.id,
            )
            return holding.id
        except Exception as exc:
            logger.error(
                "portfolio_add_holding_failed portfolio_id=%s symbol=%s shares=%s avg_cost=%s error=%s",
                portfolio_id, symbol, shares, avg_cost, exc,
            )
            raise


def update_holding(holding_id: int, shares: float, avg_cost: float) -> bool:
    """Edit an existing holding's shares/avg_cost in place (same row, same id) --
    the "Edit Position" capability. Returns False (no-op, logged) if the holding no
    longer exists rather than raising, matching `delete_holding`'s existing
    not-found-is-a-noop convention.

    Raises `ValueError` for a non-positive share count, a negative average cost, or
    a non-finite value (NaN/Infinity) -- same server-side safety net as `add_holding`.
    """
    if not math.isfinite(shares):
        raise ValueError(f"shares must be a finite number, got {shares!r}")
    if not math.isfinite(avg_cost):
        raise ValueError(f"avg_cost must be a finite number, got {avg_cost!r}")
    if shares <= 0:
        raise ValueError(f"shares must be positive, got {shares!r}")
    if avg_cost < 0:
        raise ValueError(f"avg_cost can't be negative, got {avg_cost!r}")
    with get_session() as session:
        try:
            holding = session.get(Holding, holding_id)
            if holding is None:
                logger.warning("portfolio_update_holding_not_found holding_id=%s", holding_id)
                return False
            old_shares, old_avg_cost = holding.shares, holding.avg_cost
            holding.shares = shares
            holding.avg_cost = avg_cost
            _touch_portfolio(session, holding.portfolio_id)
            logger.info(
                "portfolio_update_holding holding_id=%s old_shares=%s old_avg_cost=%s new_shares=%s new_avg_cost=%s",
                holding_id, old_shares, old_avg_cost, shares, avg_cost,
            )
            return True
        except Exception as exc:
            logger.error("portfolio_update_holding_failed holding_id=%s error=%s", holding_id, exc)
            raise


def delete_holding(holding_id: int) -> None:
    """Remove a holding by id."""
    with get_session() as session:
        try:
            holding = session.get(Holding, holding_id)
            if holding is not None:
                logger.info(
                    "portfolio_delete_holding holding_id=%s portfolio_id=%s symbol=%s",
                    holding_id, holding.portfolio_id, holding.ticker.symbol,
                )
                portfolio_id = holding.portfolio_id
                session.delete(holding)
                _touch_portfolio(session, portfolio_id)
            else:
                logger.warning("portfolio_delete_holding_not_found holding_id=%s", holding_id)
        except Exception as exc:
            logger.error("portfolio_delete_holding_failed holding_id=%s error=%s", holding_id, exc)
            raise


def aggregate_shares_by_symbol(holdings: list[dict]) -> dict[str, float]:
    """Sum shares per symbol across every lot (see `add_holding`'s docstring for why a
    symbol can legitimately have more than one `Holding` row). Portfolio-level totals
    (value, weights, allocation) must be built from this, not from a plain
    `{h["symbol"]: h["shares"] for h in holdings}` dict comprehension -- that silently
    drops every lot but the last one for any symbol held via more than one purchase,
    understating the true position size. This was a real, confirmed bug in
    `pages/3_Portfolio.py` (see PORTFOLIO_IMPLEMENTATION_LOG.md), fixed here so the
    aggregation logic is unit-testable and shared, not re-implemented in the page.
    """
    totals: dict[str, float] = {}
    for h in holdings:
        totals[h["symbol"]] = totals.get(h["symbol"], 0.0) + h["shares"]
    return totals
