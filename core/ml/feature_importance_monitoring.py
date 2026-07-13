"""Phase 2 Step 10: Feature Importance Monitoring -- persists permutation/SHAP/gain
importance per experiment (querying across runs, unlike Phase 3's per-run JSON+PNG
evaluation artifacts) and flags significant drift between consecutive experiments.

Reuses `core.ml.feature_selection.compute_permutation_importance` and
`core.ml.evaluation.generate_feature_importance`/`generate_shap_summary` to *produce*
the importance values -- this module only persists and tracks them over time, it does
not recompute importance itself.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from sqlalchemy import select

from core.config import get_logger
from core.database import FeatureImportanceSnapshot

logger = get_logger(__name__)

IMPORTANCE_TYPES = ("permutation", "shap", "gain")


def record_importance_snapshot(
    session, experiment_id: str, importance_type: str, importances: dict[str, float] | pd.Series,
) -> int:
    """Persist one experiment's importance values for every feature in `importances`.
    `importance_type` must be one of the closed set this module tracks -- an ad hoc
    string here would silently fragment the same logical measure across inconsistent
    labels."""
    if importance_type not in IMPORTANCE_TYPES:
        raise ValueError(f"importance_type {importance_type!r} not in {IMPORTANCE_TYPES}")

    count = 0
    for feature_name, value in importances.items():  # dict and pd.Series both support .items()
        session.add(
            FeatureImportanceSnapshot(
                experiment_id=experiment_id, feature_name=feature_name,
                importance_type=importance_type, value=float(value),
            )
        )
        count += 1
    session.flush()
    logger.info("Feature importance snapshot: experiment=%s type=%s -- %d feature(s) recorded", experiment_id, importance_type, count)
    return count


def get_importance_history(session, feature_name: str, importance_type: str) -> pd.DataFrame:
    """Every recorded snapshot for one (feature, importance_type), ordered by when it
    was computed -- the time series a drift check or a dashboard would read."""
    rows = session.execute(
        select(FeatureImportanceSnapshot)
        .where(FeatureImportanceSnapshot.feature_name == feature_name, FeatureImportanceSnapshot.importance_type == importance_type)
        .order_by(FeatureImportanceSnapshot.computed_at)
    ).scalars().all()
    if not rows:
        return pd.DataFrame(columns=["experiment_id", "value", "computed_at"])
    return pd.DataFrame([{"experiment_id": r.experiment_id, "value": r.value, "computed_at": r.computed_at} for r in rows])


@dataclass
class DriftAlert:
    feature_name: str
    importance_type: str
    previous_value: float
    current_value: float
    relative_change: float
    significant: bool


def detect_feature_drift(session, feature_name: str, importance_type: str, drift_threshold: float = 0.5) -> DriftAlert | None:
    """Compare a feature's two most recent importance snapshots. `relative_change` is
    signed (current - previous) / |previous|; `significant` is True when its magnitude
    exceeds `drift_threshold`. Returns None (not a fabricated zero-drift result) if
    fewer than two snapshots exist yet -- there's nothing to compare drift against.
    """
    history = get_importance_history(session, feature_name, importance_type)
    if len(history) < 2:
        return None

    previous_value = float(history.iloc[-2]["value"])
    current_value = float(history.iloc[-1]["value"])
    if previous_value == 0:
        # Avoid a divide-by-zero; any nonzero current value from a zero baseline is
        # itself the significant event, reported as such rather than as +/-inf.
        relative_change = float("inf") if current_value != 0 else 0.0
    else:
        relative_change = (current_value - previous_value) / abs(previous_value)

    significant = abs(relative_change) > drift_threshold
    alert = DriftAlert(
        feature_name=feature_name, importance_type=importance_type,
        previous_value=previous_value, current_value=current_value,
        relative_change=relative_change, significant=significant,
    )
    if significant:
        logger.warning(
            "Feature importance drift alert: %s (%s) changed %.1f%% (%.4f -> %.4f)",
            feature_name, importance_type, relative_change * 100, previous_value, current_value,
        )
    return alert


def detect_all_drift(session, importance_type: str, drift_threshold: float = 0.5) -> list[DriftAlert]:
    """Run `detect_feature_drift` for every feature that has at least two snapshots of
    `importance_type` -- the automatic "alert on significant shifts" the directive
    asks for, surfaced as a list of alerts (each already logged if significant) rather
    than a push notification, consistent with how this codebase surfaces other
    automated findings (e.g. the Phase 3 generalization gate)."""
    feature_names = session.execute(
        select(FeatureImportanceSnapshot.feature_name)
        .where(FeatureImportanceSnapshot.importance_type == importance_type)
        .distinct()
    ).scalars().all()

    alerts = []
    for feature_name in feature_names:
        alert = detect_feature_drift(session, feature_name, importance_type, drift_threshold)
        if alert is not None:
            alerts.append(alert)
    return alerts
