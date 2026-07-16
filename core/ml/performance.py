"""Explainable-AI platform phase, Phase 5: historical accuracy reporting over the
`predictions` table (see core.ml.prediction_tracking for the write/resolve paths).
Every metric here comes from real, resolved (actual_direction IS NOT NULL) rows --
never estimated or fabricated. A bucket with no resolved rows yet is reported as
`None`/"Insufficient data", never silently defaulted to a plausible-looking number.
"""

from __future__ import annotations

from dataclasses import dataclass

from sklearn.metrics import f1_score, precision_score, recall_score
from sqlalchemy import select

from core.database import Prediction, Ticker, get_session


@dataclass
class PerformanceStats:
    n: int  # resolved prediction count this stat is based on
    accuracy: float | None
    precision: float | None
    recall: float | None
    f1: float | None


def _compute_stats(y_true: list[int], y_pred: list[int]) -> PerformanceStats:
    n = len(y_true)
    if n == 0:
        return PerformanceStats(n=0, accuracy=None, precision=None, recall=None, f1=None)
    accuracy = sum(1 for t, p in zip(y_true, y_pred) if t == p) / n
    # zero_division=0 rather than a warning-raising default: an all-one-class bucket
    # (e.g. every resolved prediction in a tiny bucket happened to be "up") is a real,
    # if unhelpful, small-sample state -- reported as 0.0, not crashed on or hidden.
    precision = precision_score(y_true, y_pred, zero_division=0)
    recall = recall_score(y_true, y_pred, zero_division=0)
    f1 = f1_score(y_true, y_pred, zero_division=0)
    return PerformanceStats(n=n, accuracy=accuracy, precision=float(precision), recall=float(recall), f1=float(f1))


def _resolved_rows(symbol: str | None = None, model_version: str | None = None) -> list[Prediction]:
    with get_session() as session:
        query = select(Prediction).where(Prediction.actual_direction.is_not(None))
        if symbol is not None:
            ticker = session.execute(select(Ticker).where(Ticker.symbol == symbol.upper())).scalar_one_or_none()
            if ticker is None:
                return []
            query = query.where(Prediction.ticker_id == ticker.id)
        if model_version is not None:
            query = query.where(Prediction.model_version == model_version)
        rows = session.execute(query).scalars().all()
        # Detach the plain values we need before the session closes.
        return [
            type("ResolvedRow", (), {
                "predicted_direction": r.predicted_direction,
                "actual_direction": r.actual_direction,
                "confidence_level": r.confidence_level,
                "market_regime": r.market_regime,
            })()
            for r in rows
        ]


def overall_performance(symbol: str | None = None, model_version: str | None = None) -> PerformanceStats:
    rows = _resolved_rows(symbol, model_version)
    return _compute_stats([r.actual_direction for r in rows], [r.predicted_direction for r in rows])


def performance_by_confidence_bucket(symbol: str | None = None, model_version: str | None = None) -> dict[str, PerformanceStats]:
    """One PerformanceStats per confidence_level (Very High/High/Medium/Low/Very Low)
    that has at least one resolved row. Buckets with zero resolved rows are simply
    absent from the returned dict -- never fabricated with n=0 stats presented as real."""
    rows = _resolved_rows(symbol, model_version)
    by_bucket: dict[str, list] = {}
    for r in rows:
        if r.confidence_level is None:
            continue
        by_bucket.setdefault(r.confidence_level, []).append(r)
    return {
        level: _compute_stats([r.actual_direction for r in bucket_rows], [r.predicted_direction for r in bucket_rows])
        for level, bucket_rows in by_bucket.items()
    }


def performance_by_market_regime(symbol: str | None = None, model_version: str | None = None) -> dict[str, PerformanceStats]:
    """One PerformanceStats per market_regime label recorded at prediction time."""
    rows = _resolved_rows(symbol, model_version)
    by_regime: dict[str, list] = {}
    for r in rows:
        if r.market_regime is None:
            continue
        by_regime.setdefault(r.market_regime, []).append(r)
    return {
        regime: _compute_stats([r.actual_direction for r in regime_rows], [r.predicted_direction for r in regime_rows])
        for regime, regime_rows in by_regime.items()
    }


def prediction_timeline(symbol: str, model_version: str | None = None, limit: int = 60) -> list[dict]:
    """Explainable-AI platform phase, Phase 10: every recorded prediction for `symbol`
    (resolved or not -- a dashboard timeline should show pending predictions too, not
    just graded ones), newest first, capped at `limit` rows. Unlike `_resolved_rows`
    (which filters to graded outcomes for accuracy math), this is for charting a
    prediction-vs-outcome history over time, so unresolved rows are included with
    `actual_direction=None` rather than dropped."""
    with get_session() as session:
        ticker = session.execute(select(Ticker).where(Ticker.symbol == symbol.upper())).scalar_one_or_none()
        if ticker is None:
            return []
        query = select(Prediction).where(Prediction.ticker_id == ticker.id)
        if model_version is not None:
            query = query.where(Prediction.model_version == model_version)
        query = query.order_by(Prediction.date.desc()).limit(limit)
        rows = session.execute(query).scalars().all()
        return [
            {
                "date": r.date,
                "predicted_direction": r.predicted_direction,
                "probability": r.probability,
                "actual_direction": r.actual_direction,
                "confidence_level": r.confidence_level,
                "model_version": r.model_version,
            }
            for r in rows
        ]
