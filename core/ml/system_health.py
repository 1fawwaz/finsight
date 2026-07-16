"""Explainable-AI platform phase, Phase 10: lightweight system health checks for the AI
Dashboard. Every check reuses an existing subsystem (the DB session, the model
registry) rather than adding a new one -- this module only aggregates their real,
current status into a small, testable list."""

from __future__ import annotations

from dataclasses import dataclass

from core.config import get_logger

logger = get_logger(__name__)


@dataclass
class HealthCheck:
    name: str
    ok: bool
    detail: str


def check_database_connectivity() -> HealthCheck:
    from sqlalchemy import text

    from core.database import get_session

    try:
        with get_session() as session:
            session.execute(text("SELECT 1"))
        return HealthCheck("Database connectivity", True, "OK")
    except Exception as exc:
        logger.warning("Database connectivity check failed: %s", exc)
        return HealthCheck("Database connectivity", False, f"Error: {exc}")


def check_active_model(model_name: str) -> HealthCheck:
    from core.ml.registry import get_active_model

    try:
        model, entry = get_active_model(model_name)
        if model is None:
            return HealthCheck("Active model registered", False, "No active model registered for this name.")
        return HealthCheck("Active model registered", True, f"{entry.version} ({entry.status})")
    except Exception as exc:
        logger.warning("Active model check failed for %s: %s", model_name, exc)
        return HealthCheck("Active model registered", False, f"Error: {exc}")


def check_price_data(price_df) -> HealthCheck:
    if price_df is None or price_df.empty:
        return HealthCheck("Price data available", False, "No price history loaded for this symbol.")
    return HealthCheck("Price data available", True, f"{len(price_df)} bar(s) loaded.")


def check_prediction_generated(result) -> HealthCheck:
    if result is None or not result.has_prediction:
        return HealthCheck("Prediction generation", False, "Could not generate a prediction for this symbol.")
    return HealthCheck("Prediction generation", True, f"Source: {result.model_source}")


def run_all_checks(model_name: str, price_df, result) -> list[HealthCheck]:
    """All checks always run and always return a HealthCheck -- a failing check is
    itself the useful, real signal, never a reason to skip the rest."""
    return [
        check_database_connectivity(),
        check_active_model(model_name),
        check_price_data(price_df),
        check_prediction_generated(result),
    ]
