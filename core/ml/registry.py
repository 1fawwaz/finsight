"""Phase 3 Step 2.4: Model Registry -- persists a selected model's serialized artifact
and full lineage (dataset version, feature version, hyperparameters, metrics, git
commit) so a deployed model is always traceable back to exactly what produced it.
"""

from __future__ import annotations

import json
import subprocess

import joblib
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
