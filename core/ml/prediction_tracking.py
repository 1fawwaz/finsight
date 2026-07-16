"""Explainable-AI platform phase, Phase 5: Historical Intelligence -- write and
resolution paths for the `predictions` table (defined since an early phase, confirmed
completely unused anywhere in the repo during the Phase 1 audit).

Two functions, two responsibilities:
- `record_prediction`: called once a `PredictionResult` has been generated, stores it.
- `resolve_pending_outcomes`: a separate, idempotent job (call it from a scheduled task,
  or simply each time a page loads -- it's cheap and a no-op when nothing's resolvable)
  that fills in `actual_direction` for predictions whose target session has since
  actually happened, using the same historical price data already stored by the
  existing `core.data_ingestion` pipeline -- never re-deriving "what actually happened"
  from anything but real, already-ingested OHLCV rows.
"""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime, timezone

from sqlalchemy import select

from core.config import get_logger
from core.database import Prediction, Ticker, get_session
from core.market_status import previous_trading_day
from core.queries import get_price_history

logger = get_logger(__name__)


def record_prediction(symbol: str, target_date: date_type, result) -> bool:
    """Persist one prediction. `result` is a
    `core.ml.prediction_service.PredictionResult` with `result.has_prediction` True.
    Uniqueness of (ticker, target_date, model_version) is enforced here at the
    application layer (query-then-insert) rather than a DB constraint -- see
    `core.database.Prediction`'s own docstring for why. Returns True if a new row was
    inserted, False if one already existed (e.g. the user reloaded the page for the
    same symbol on the same day) -- a duplicate is not an error, just a no-op.
    """
    if not result.has_prediction:
        raise ValueError("Cannot record a prediction that wasn't actually made (result.has_prediction is False).")

    model_version = result.model_version or f"{result.model_name}_unversioned"

    with get_session() as session:
        ticker = session.execute(select(Ticker).where(Ticker.symbol == symbol.upper())).scalar_one_or_none()
        if ticker is None:
            logger.warning("record_prediction: no Ticker row for %s, skipping", symbol)
            return False

        existing = session.execute(
            select(Prediction).where(
                Prediction.ticker_id == ticker.id,
                Prediction.date == target_date,
                Prediction.model_version == model_version,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return False

        row = Prediction(
            ticker_id=ticker.id,
            date=target_date,
            model_version=model_version,
            predicted_direction=1 if result.confidence.prediction_class == "UP" else 0,
            probability=result.confidence.probability_up,
            confidence_score=result.confidence.confidence_score,
            confidence_level=result.confidence.confidence_level,
            dataset_version=result.dataset_version,
            market_regime=result.risk.market_regime if result.risk is not None else None,
            recorded_at=datetime.now(timezone.utc),
        )
        session.add(row)
        session.flush()
        logger.info(
            "Recorded prediction: symbol=%s date=%s model=%s direction=%s prob=%.3f confidence=%s",
            symbol, target_date, model_version, row.predicted_direction, row.probability, row.confidence_level,
        )
        return True


def resolve_pending_outcomes(symbol: str | None = None) -> int:
    """Fill in `actual_direction` (and correctness, derivable as predicted_direction ==
    actual_direction) for every unresolved prediction whose target session's real close
    -- and the real close of the trading day before it -- are now both present in the
    existing `prices` table. Direction definition matches `core.ml_model.build_labels`
    exactly: 1 if the target session's close is higher than the prior session's close.
    Returns the number of rows resolved this call."""
    with get_session() as session:
        query = select(Prediction).where(Prediction.actual_direction.is_(None))
        if symbol is not None:
            ticker = session.execute(select(Ticker).where(Ticker.symbol == symbol.upper())).scalar_one_or_none()
            if ticker is None:
                return 0
            query = query.where(Prediction.ticker_id == ticker.id)
        pending = session.execute(query).scalars().all()
        if not pending:
            return 0

        # Group by ticker so each symbol's price history is loaded once, not once per
        # pending row.
        by_ticker: dict[int, list[Prediction]] = {}
        for row in pending:
            by_ticker.setdefault(row.ticker_id, []).append(row)

        resolved_count = 0
        for ticker_id, rows in by_ticker.items():
            ticker = session.get(Ticker, ticker_id)
            if ticker is None:
                continue
            history = get_price_history(ticker.symbol)
            if history.empty:
                continue
            close_by_date = {ts.date(): float(c) for ts, c in history["close"].items()}

            for row in rows:
                prior_date = previous_trading_day(row.date)
                if row.date not in close_by_date or prior_date not in close_by_date:
                    continue  # target session (or the prior close it's compared against) hasn't happened/synced yet
                actual_up = close_by_date[row.date] > close_by_date[prior_date]
                row.actual_direction = 1 if actual_up else 0
                row.resolved_at = datetime.now(timezone.utc)
                resolved_count += 1

        session.flush()
        if resolved_count:
            logger.info("resolve_pending_outcomes: resolved %d prediction(s)", resolved_count)
        return resolved_count
