"""Phase 2 Step 11: Experiment Tracking.

Reuses `ml_training_runs` (Phase 3's `MLTrainingRun`, already the per-trial experiment
log every training call writes to) rather than introducing a second, competing
"experiment" table -- extended additively (Step 11's schema change, see
`core.database._ADDITIVE_COLUMN_MIGRATIONS`) with the fields the directive's field list
needed that Phase 3 didn't originally track: git commit, training duration, prediction
latency, calibration results, feature importance, and free-text notes.

Immutability is structural, not a runtime check: this module exposes `log_experiment`
(always an INSERT) and read-only query functions -- there is no `update_experiment`
function anywhere in this module's public surface, so there is nothing to call that
would modify a historical row.
"""

from __future__ import annotations

import json

import pandas as pd
from sqlalchemy import select

from core.config import get_logger
from core.database import MLTrainingRun, get_session
from core.ml.registry import _git_commit_hash

logger = get_logger(__name__)


def log_experiment(
    model_family: str,
    dataset_version: str,
    feature_version: str,
    hyperparameters: dict,
    metrics: dict,
    fold_metrics: list[dict] | None = None,
    trial_number: int = -1,
    training_duration_seconds: float | None = None,
    prediction_latency_ms: float | None = None,
    calibration_results: dict | None = None,
    feature_importance: dict | None = None,
    notes: str | None = None,
) -> MLTrainingRun:
    """Record one experiment. Always an INSERT (`session.add`) -- never looks up or
    updates an existing row, which is what makes every experiment immutable once
    logged. `git_commit_hash` is captured automatically (reusing
    `core.ml.registry._git_commit_hash`, the same helper the model registry already
    uses -- not a second git-inspection implementation).
    """
    with get_session() as session:
        run = MLTrainingRun(
            model_family=model_family,
            trial_number=trial_number,
            dataset_version=dataset_version,
            feature_version=feature_version,
            hyperparameters_json=json.dumps(hyperparameters, default=str),
            metrics_json=json.dumps(metrics, default=str),
            fold_metrics_json=json.dumps(fold_metrics or [], default=str),
            git_commit_hash=_git_commit_hash(),
            training_duration_seconds=training_duration_seconds,
            prediction_latency_ms=prediction_latency_ms,
            calibration_results_json=json.dumps(calibration_results, default=str) if calibration_results is not None else None,
            feature_importance_json=json.dumps(feature_importance, default=str) if feature_importance is not None else None,
            notes=notes,
        )
        session.add(run)
        session.flush()
        logger.info(
            "Experiment logged: id=%d family=%s dataset=%s feature=%s commit=%s",
            run.id, model_family, dataset_version, feature_version, run.git_commit_hash,
        )
        return run


def get_experiment(session, experiment_id: int) -> MLTrainingRun | None:
    return session.get(MLTrainingRun, experiment_id)


def get_experiment_history(
    session, model_family: str | None = None, dataset_version: str | None = None,
) -> pd.DataFrame:
    """Every logged experiment (optionally filtered), newest first -- the full,
    immutable audit trail. Never drops or overwrites a row; a "bad" experiment stays
    in this history forever, exactly like a failed Optuna trial already does in the
    pre-existing `ml_training_runs` usage.

    Orders by `created_at` then `id`, both descending: SQLite's `CURRENT_TIMESTAMP`
    only has second-level resolution, so two experiments logged within the same
    second (routine for an automated training loop, observed directly while testing
    this function) would otherwise sort ambiguously on `created_at` alone. `id` is
    monotonically increasing with insertion order and breaks the tie correctly.
    """
    query = select(MLTrainingRun)
    if model_family is not None:
        query = query.where(MLTrainingRun.model_family == model_family)
    if dataset_version is not None:
        query = query.where(MLTrainingRun.dataset_version == dataset_version)
    rows = session.execute(query.order_by(MLTrainingRun.created_at.desc(), MLTrainingRun.id.desc())).scalars().all()

    if not rows:
        return pd.DataFrame()

    records = []
    for r in rows:
        records.append(
            {
                "id": r.id,
                "model_family": r.model_family,
                "trial_number": r.trial_number,
                "dataset_version": r.dataset_version,
                "feature_version": r.feature_version,
                "hyperparameters": json.loads(r.hyperparameters_json),
                "metrics": json.loads(r.metrics_json),
                "git_commit_hash": r.git_commit_hash,
                "training_duration_seconds": r.training_duration_seconds,
                "prediction_latency_ms": r.prediction_latency_ms,
                "calibration_results": json.loads(r.calibration_results_json) if r.calibration_results_json else None,
                "feature_importance": json.loads(r.feature_importance_json) if r.feature_importance_json else None,
                "notes": r.notes,
                "created_at": r.created_at,
            }
        )
    return pd.DataFrame(records)
