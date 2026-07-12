"""Tests for core.ml.registry: model artifact persistence and lineage tracking."""

import pytest

from core.ml.registry import get_active_model, load_model_by_version, register_model


class _FakeModel:
    """A minimal picklable stand-in so these tests don't need a real fitted estimator."""

    def __init__(self, tag: str):
        self.tag = tag

    def predict(self, X):
        return [1] * len(X)


def test_register_model_persists_and_reloads_identically(temp_db, tmp_path, monkeypatch):
    import core.ml.registry as registry_module

    monkeypatch.setattr(registry_module, "MODEL_ARTIFACT_DIR", tmp_path)
    model = _FakeModel("original")

    entry = register_model(
        model, "test_model", "random_forest", "dataset_v1", "features_v1",
        {"max_depth": 3}, {"roc_auc": 0.51}, activate=True,
    )
    assert entry.version == "test_model_v1"
    assert entry.is_active is True

    reloaded = load_model_by_version("test_model_v1")
    assert reloaded.tag == "original"


def test_register_model_deactivates_prior_active_entry(temp_db, tmp_path, monkeypatch):
    import core.ml.registry as registry_module

    monkeypatch.setattr(registry_module, "MODEL_ARTIFACT_DIR", tmp_path)

    register_model(_FakeModel("v1"), "test_model", "xgboost", "ds1", "fs1", {}, {"roc_auc": 0.50}, activate=True)
    register_model(_FakeModel("v2"), "test_model", "xgboost", "ds1", "fs1", {}, {"roc_auc": 0.52}, activate=True)

    active_model, active_entry = get_active_model("test_model")
    assert active_entry.version == "test_model_v2"
    assert active_model.tag == "v2"

    # v1 must still exist in the registry (never deleted), just no longer active.
    v1 = load_model_by_version("test_model_v1")
    assert v1.tag == "v1"


def test_register_model_without_activating_leaves_prior_active_entry_alone(temp_db, tmp_path, monkeypatch):
    import core.ml.registry as registry_module

    monkeypatch.setattr(registry_module, "MODEL_ARTIFACT_DIR", tmp_path)

    register_model(_FakeModel("v1"), "test_model", "xgboost", "ds1", "fs1", {}, {"roc_auc": 0.50}, activate=True)
    register_model(_FakeModel("v2_candidate"), "test_model", "xgboost", "ds1", "fs1", {}, {"roc_auc": 0.48}, activate=False)

    active_model, active_entry = get_active_model("test_model")
    assert active_entry.version == "test_model_v1"


def test_get_active_model_returns_none_when_nothing_registered(temp_db, tmp_path, monkeypatch):
    import core.ml.registry as registry_module

    monkeypatch.setattr(registry_module, "MODEL_ARTIFACT_DIR", tmp_path)
    model, entry = get_active_model("never_registered")
    assert model is None
    assert entry is None


def test_load_model_by_version_unknown_version_raises(temp_db, tmp_path, monkeypatch):
    import core.ml.registry as registry_module

    monkeypatch.setattr(registry_module, "MODEL_ARTIFACT_DIR", tmp_path)
    with pytest.raises(ValueError, match="No model version"):
        load_model_by_version("does_not_exist")


def test_version_numbers_increment_per_model_name(temp_db, tmp_path, monkeypatch):
    import core.ml.registry as registry_module

    monkeypatch.setattr(registry_module, "MODEL_ARTIFACT_DIR", tmp_path)
    e1 = register_model(_FakeModel("a"), "model_a", "xgboost", "ds", "fs", {}, {}, activate=False)
    e2 = register_model(_FakeModel("b"), "model_b", "xgboost", "ds", "fs", {}, {}, activate=False)
    e3 = register_model(_FakeModel("a2"), "model_a", "xgboost", "ds", "fs", {}, {}, activate=False)
    assert e1.version == "model_a_v1"
    assert e2.version == "model_b_v1"  # independent numbering per model_name
    assert e3.version == "model_a_v2"
