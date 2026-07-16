"""Phase 3 Step 2.4: Model Registry -- persists a selected model's serialized artifact
and full lineage (dataset version, feature version, hyperparameters, metrics, git
commit) so a deployed model is always traceable back to exactly what produced it.
"""

from __future__ import annotations

import json
import subprocess

import joblib
import numpy as np
from sqlalchemy import select, update

from core.config import BASE_DIR, get_logger
from core.database import MLModelRegistry, get_session

logger = get_logger(__name__)

MODEL_ARTIFACT_DIR = BASE_DIR / "data" / "ml_models"
MODEL_ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)


def _git_commit_hash() -> str | None:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=BASE_DIR, capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception as exc:  # git absent/unavailable must never break registration
        logger.warning("Could not determine git commit hash: %s", exc)
    return None


def _validate_safe_identifier(value: str, field_name: str) -> None:
    """model_name/version are used to build filesystem paths under MODEL_ARTIFACT_DIR --
    reject anything that could escape that directory (path separators, `..`, a null
    byte) rather than trusting the caller. Currently every call site passes a
    hardcoded or internally-generated string, but this is exactly the kind of value a
    future REST API (a stated Future Scalability goal) would take from a request, so
    it's validated here rather than assumed safe forever."""
    if not value or not isinstance(value, str):
        raise ValueError(f"{field_name} must be a non-empty string.")
    if any(bad in value for bad in ("/", "\\", "..", "\x00")):
        raise ValueError(f"{field_name} contains unsafe path characters: {value!r}")


def _next_model_version(model_name: str) -> str:
    with get_session() as session:
        count = len(session.execute(select(MLModelRegistry).where(MLModelRegistry.model_name == model_name)).scalars().all())
        return f"{model_name}_v{count + 1}"


def register_model(
    model,
    model_name: str,
    model_family: str,
    dataset_version: str,
    feature_version: str,
    hyperparameters: dict,
    metrics: dict,
    activate: bool = True,
) -> MLModelRegistry:
    """Serialize `model` to disk and persist its full lineage. If `activate`, marks this
    the active model for `model_name` and deactivates any prior active entry for it --
    never deletes a prior entry, so registry history is never lost."""
    _validate_safe_identifier(model_name, "model_name")
    version = _next_model_version(model_name)
    filename = f"{version}.joblib"
    artifact_path = MODEL_ARTIFACT_DIR / filename
    joblib.dump(model, artifact_path)

    with get_session() as session:
        if activate:
            session.execute(update(MLModelRegistry).where(MLModelRegistry.model_name == model_name).values(is_active=False))
        entry = MLModelRegistry(
            model_name=model_name,
            model_family=model_family,
            version=version,
            dataset_version=dataset_version,
            feature_version=feature_version,
            hyperparameters_json=json.dumps(hyperparameters),
            metrics_json=json.dumps(metrics),
            # Stored as a bare filename, resolved against MODEL_ARTIFACT_DIR at load
            # time -- not an absolute path. An absolute path baked in at registration
            # time (e.g. a Windows host path) is meaningless in a different environment
            # reading the same DB through a volume mount (e.g. the Linux container),
            # even though the underlying file is identical on disk. Confirmed by an
            # actual cross-environment failure during Docker end-to-end verification.
            artifact_path=filename,
            git_commit_hash=_git_commit_hash(),
            is_active=activate,
        )
        session.add(entry)
        session.flush()
        logger.info("Registered model %s (family=%s, active=%s) -> %s", version, model_family, activate, artifact_path)
        return entry


def load_model_by_version(version: str):
    """Load the serialized model for a specific registry version."""
    with get_session() as session:
        entry = session.execute(select(MLModelRegistry).where(MLModelRegistry.version == version)).scalar_one_or_none()
        if entry is None:
            raise ValueError(f"No model version {version!r} in the registry.")
        artifact_path = MODEL_ARTIFACT_DIR / entry.artifact_path
    if not artifact_path.exists():
        raise FileNotFoundError(f"Registry entry {version!r} exists but its artifact is missing: {artifact_path}")
    return joblib.load(artifact_path)


def get_active_model(model_name: str):
    """Returns (model, registry_entry) for the currently active model named
    `model_name`, or (None, None) if nothing is active."""
    with get_session() as session:
        entry = session.execute(
            select(MLModelRegistry).where(MLModelRegistry.model_name == model_name, MLModelRegistry.is_active.is_(True))
        ).scalar_one_or_none()
        if entry is None:
            return None, None
        artifact_path = MODEL_ARTIFACT_DIR / entry.artifact_path
    if not artifact_path.exists():
        raise FileNotFoundError(f"Active registry entry for {model_name!r} exists but its artifact is missing: {artifact_path}")
    return joblib.load(artifact_path), entry


# --- Explainable-AI platform phase: status lifecycle + serving-time calibration --------

MODEL_STATUSES = ("active", "testing", "archived")


def set_model_status(version: str, status: str) -> None:
    """Set a registry entry's lifecycle status. Independent of `is_active` (which
    controls which model `get_active_model` serves) -- a model can be `is_active=True`
    and `status="testing"` simultaneously (e.g. a canary), though the common case is
    active+"active" and every superseded version moved to "archived"."""
    if status not in MODEL_STATUSES:
        raise ValueError(f"status must be one of {MODEL_STATUSES}, got {status!r}")
    with get_session() as session:
        entry = session.execute(select(MLModelRegistry).where(MLModelRegistry.version == version)).scalar_one_or_none()
        if entry is None:
            raise ValueError(f"No model version {version!r} in the registry.")
        entry.status = status
        session.flush()
        logger.info("Set registry status for %s -> %s", version, status)


def fit_and_store_calibration(model_name: str) -> float:
    """Fit temperature scaling (core.ml.calibration.fit_temperature) for the currently
    active model named `model_name`, using its own registered feature_version's
    validation split (never the model's training data, and never the held-out test
    split either -- both would misstate real-world miscalibration) as the calibration
    set. Persists the resulting scalar to `MLModelRegistry.calibration_temperature` and
    returns it.

    Reuses core.ml.calibration (Phase 2 Step 7) and core.ml.cv's chronological split
    (Phase 3) rather than reimplementing either -- this function's only new logic is
    "reload this model's own val split and call the existing fitter."
    """
    from core.ml.calibration import fit_temperature
    from core.ml.cv import chronological_train_val_test_split
    from core.ml.feature_pipeline import load_feature_set

    model, entry = get_active_model(model_name)
    if model is None:
        raise ValueError(f"No active model registered under {model_name!r} -- nothing to calibrate.")

    features, labels = load_feature_set(entry.feature_version)
    split = chronological_train_val_test_split(features)
    X_val = features.loc[split.val_index]
    y_val = labels.loc[split.val_index]
    if hasattr(model, "feature_names_in_"):
        X_val = X_val.reindex(columns=model.feature_names_in_)
    valid_rows = ~X_val.isna().any(axis=1)
    X_val, y_val = X_val[valid_rows], y_val[valid_rows]
    if len(X_val) < 30:
        raise ValueError(f"Only {len(X_val)} clean validation rows available for {model_name!r} -- too few to fit a stable calibration.")

    raw_val_proba = model.predict_proba(X_val)[:, 1]
    temperature = fit_temperature(y_val, raw_val_proba)

    with get_session() as session:
        db_entry = session.execute(select(MLModelRegistry).where(MLModelRegistry.version == entry.version)).scalar_one()
        db_entry.calibration_temperature = temperature
        session.flush()
    logger.info("Fit calibration temperature=%.4f for %s (n_val=%d)", temperature, entry.version, len(X_val))
    return temperature


def list_registry_entries(model_name: str | None = None) -> list[dict]:
    """Read-only lineage listing for the Model Registry UI (Phase 6): every version ever
    registered for `model_name` (or every model if omitted), newest first -- unlike
    `get_active_model`, this returns full history, not just the currently active entry,
    so a user can see the status lifecycle (active/testing/archived) across versions.
    Never loads the serialized model artifact -- lineage metadata only."""
    with get_session() as session:
        query = select(MLModelRegistry)
        if model_name is not None:
            query = query.where(MLModelRegistry.model_name == model_name)
        # id.desc() as the primary sort (not just created_at.desc()): SQLite's
        # server_default=func.now() has second-level resolution, so two registrations
        # in the same second (e.g. back-to-back in a test or a fast retrain loop) would
        # otherwise tie and sort arbitrarily -- id strictly matches insertion order.
        query = query.order_by(MLModelRegistry.id.desc())
        rows = session.execute(query).scalars().all()
        return [
            {
                "model_name": r.model_name,
                "model_family": r.model_family,
                "version": r.version,
                "dataset_version": r.dataset_version,
                "feature_version": r.feature_version,
                "hyperparameters": json.loads(r.hyperparameters_json),
                "metrics": json.loads(r.metrics_json),
                "git_commit_hash": r.git_commit_hash,
                "created_at": r.created_at,
                "is_active": r.is_active,
                "status": r.status,
                "calibration_temperature": r.calibration_temperature,
            }
            for r in rows
        ]


def apply_calibration(entry: MLModelRegistry, raw_probability: float) -> tuple[float, bool]:
    """Apply a registry entry's stored calibration temperature to a raw predict_proba
    output. Returns (calibrated_probability, was_calibrated) -- `was_calibrated` is
    False (and the probability is returned unchanged) when no temperature has been fit
    yet for this version, so callers can surface "uncalibrated" honestly instead of
    silently presenting a raw probability as if it were calibrated."""
    from core.ml.calibration import _apply_temperature

    if entry.calibration_temperature is None:
        return raw_probability, False
    calibrated = float(_apply_temperature(np.array([raw_probability]), entry.calibration_temperature)[0])
    return calibrated, True
