"""Tests for core.ml.registry: model artifact persistence and lineage tracking."""

import numpy as np
import pandas as pd
import pytest

from core.ml.registry import (
    apply_calibration,
    fit_and_store_calibration,
    get_active_model,
    list_registry_entries,
    load_model_by_version,
    register_model,
    set_model_status,
)


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


@pytest.mark.parametrize("malicious_name", ["../../etc/passwd", "..\\..\\windows\\system32", "a/b", "a\\b", "..", "\x00"])
def test_register_model_rejects_path_traversal_in_model_name(temp_db, tmp_path, monkeypatch, malicious_name):
    import core.ml.registry as registry_module

    monkeypatch.setattr(registry_module, "MODEL_ARTIFACT_DIR", tmp_path)
    with pytest.raises(ValueError, match="unsafe path characters|non-empty string"):
        register_model(_FakeModel("x"), malicious_name, "xgboost", "ds", "fs", {}, {}, activate=False)

    # Nothing should have been written outside (or inside) the artifact directory.
    assert list(tmp_path.iterdir()) == []


def test_register_model_rejects_empty_model_name(temp_db, tmp_path, monkeypatch):
    import core.ml.registry as registry_module

    monkeypatch.setattr(registry_module, "MODEL_ARTIFACT_DIR", tmp_path)
    with pytest.raises(ValueError, match="non-empty string"):
        register_model(_FakeModel("x"), "", "xgboost", "ds", "fs", {}, {}, activate=False)


class TestModelStatusLifecycle:
    def test_new_registration_defaults_to_active_status(self, temp_db, tmp_path, monkeypatch):
        import core.ml.registry as registry_module

        monkeypatch.setattr(registry_module, "MODEL_ARTIFACT_DIR", tmp_path)
        register_model(_FakeModel("v1"), "status_model", "xgboost", "ds", "fs", {}, {}, activate=True)
        _, entry = get_active_model("status_model")
        assert entry.status == "active"

    def test_set_model_status_updates_the_row(self, temp_db, tmp_path, monkeypatch):
        import core.ml.registry as registry_module

        monkeypatch.setattr(registry_module, "MODEL_ARTIFACT_DIR", tmp_path)
        entry = register_model(_FakeModel("v1"), "status_model2", "xgboost", "ds", "fs", {}, {}, activate=True)
        set_model_status(entry.version, "archived")
        _, refreshed = get_active_model("status_model2")
        assert refreshed.status == "archived"

    def test_set_model_status_rejects_unknown_status(self, temp_db, tmp_path, monkeypatch):
        import core.ml.registry as registry_module

        monkeypatch.setattr(registry_module, "MODEL_ARTIFACT_DIR", tmp_path)
        entry = register_model(_FakeModel("v1"), "status_model3", "xgboost", "ds", "fs", {}, {}, activate=True)
        with pytest.raises(ValueError, match="status must be one of"):
            set_model_status(entry.version, "not_a_real_status")

    def test_set_model_status_unknown_version_raises(self, temp_db, tmp_path, monkeypatch):
        import core.ml.registry as registry_module

        monkeypatch.setattr(registry_module, "MODEL_ARTIFACT_DIR", tmp_path)
        with pytest.raises(ValueError, match="No model version"):
            set_model_status("does_not_exist", "archived")


class TestListRegistryEntries:
    def test_empty_registry_returns_empty_list(self, temp_db, tmp_path, monkeypatch):
        import core.ml.registry as registry_module

        monkeypatch.setattr(registry_module, "MODEL_ARTIFACT_DIR", tmp_path)
        assert list_registry_entries("never_registered") == []

    def test_lists_full_lineage_fields(self, temp_db, tmp_path, monkeypatch):
        import core.ml.registry as registry_module

        monkeypatch.setattr(registry_module, "MODEL_ARTIFACT_DIR", tmp_path)
        register_model(
            _FakeModel("v1"), "lineage_model", "xgboost", "dataset_v1", "features_v1",
            {"max_depth": 3}, {"roc_auc": 0.55}, activate=True,
        )
        entries = list_registry_entries("lineage_model")
        assert len(entries) == 1
        entry = entries[0]
        assert entry["version"] == "lineage_model_v1"
        assert entry["dataset_version"] == "dataset_v1"
        assert entry["feature_version"] == "features_v1"
        assert entry["hyperparameters"] == {"max_depth": 3}
        assert entry["metrics"] == {"roc_auc": 0.55}
        assert entry["status"] == "active"
        assert entry["is_active"] is True

    def test_returns_every_version_newest_first(self, temp_db, tmp_path, monkeypatch):
        import core.ml.registry as registry_module

        monkeypatch.setattr(registry_module, "MODEL_ARTIFACT_DIR", tmp_path)
        register_model(_FakeModel("v1"), "multi_model", "xgboost", "ds1", "fs1", {}, {}, activate=True)
        register_model(_FakeModel("v2"), "multi_model", "xgboost", "ds1", "fs1", {}, {}, activate=True)
        entries = list_registry_entries("multi_model")
        assert len(entries) == 2
        assert entries[0]["version"] == "multi_model_v2"  # newest first
        assert entries[1]["version"] == "multi_model_v1"
        assert entries[1]["is_active"] is False  # superseded, but still present (never deleted)

    def test_omitting_model_name_returns_every_model(self, temp_db, tmp_path, monkeypatch):
        import core.ml.registry as registry_module

        monkeypatch.setattr(registry_module, "MODEL_ARTIFACT_DIR", tmp_path)
        register_model(_FakeModel("a"), "model_a", "xgboost", "ds", "fs", {}, {}, activate=False)
        register_model(_FakeModel("b"), "model_b", "xgboost", "ds", "fs", {}, {}, activate=False)
        entries = list_registry_entries()
        assert {e["model_name"] for e in entries} == {"model_a", "model_b"}

    def test_status_lifecycle_reflected_in_listing(self, temp_db, tmp_path, monkeypatch):
        import core.ml.registry as registry_module

        monkeypatch.setattr(registry_module, "MODEL_ARTIFACT_DIR", tmp_path)
        entry = register_model(_FakeModel("v1"), "status_listing_model", "xgboost", "ds", "fs", {}, {}, activate=True)
        set_model_status(entry.version, "testing")
        listed = list_registry_entries("status_listing_model")[0]
        assert listed["status"] == "testing"


class TestApplyCalibration:
    def test_no_temperature_fit_yet_returns_raw_unchanged_and_flags_uncalibrated(self):
        entry = type("Entry", (), {"calibration_temperature": None})()
        calibrated, was_calibrated = apply_calibration(entry, 0.73)
        assert calibrated == 0.73
        assert was_calibrated is False

    def test_temperature_of_one_is_a_near_no_op(self):
        entry = type("Entry", (), {"calibration_temperature": 1.0})()
        calibrated, was_calibrated = apply_calibration(entry, 0.7)
        assert calibrated == pytest.approx(0.7, abs=1e-6)
        assert was_calibrated is True

    def test_high_temperature_flattens_toward_half(self):
        entry = type("Entry", (), {"calibration_temperature": 20.0})()
        calibrated, was_calibrated = apply_calibration(entry, 0.9)
        assert was_calibrated is True
        assert calibrated < 0.9
        assert calibrated > 0.5


class _ProbModel:
    """Module-level (not nested) so joblib can pickle it in TestFitAndStoreCalibration."""

    def predict_proba(self, X):
        # deterministic, mildly-informative probabilities so temperature fitting
        # has something non-degenerate to work with
        vals = 0.5 + 0.1 * np.tanh(X["f1"].to_numpy())
        return np.column_stack([1 - vals, vals])


class TestFitAndStoreCalibration:
    def _synthetic_features_labels(self, n_dates: int = 200):
        rng = np.random.default_rng(7)
        dates = pd.date_range("2024-01-01", periods=n_dates, freq="B")
        index = pd.MultiIndex.from_product([["FAKE.NS"], dates], names=["symbol", "date"])
        features = pd.DataFrame({"f1": rng.normal(size=n_dates), "f2": rng.normal(size=n_dates)}, index=index)
        labels = pd.Series(rng.integers(0, 2, size=n_dates), index=index)
        return features, labels

    def test_fits_and_persists_a_temperature(self, temp_db, tmp_path, monkeypatch):
        import core.ml.registry as registry_module

        monkeypatch.setattr(registry_module, "MODEL_ARTIFACT_DIR", tmp_path)
        register_model(_ProbModel(), "calib_model", "logistic", "ds1", "fs1", {}, {"roc_auc": 0.55}, activate=True)

        features, labels = self._synthetic_features_labels()
        monkeypatch.setattr("core.ml.feature_pipeline.load_feature_set", lambda feature_version: (features, labels))

        temperature = fit_and_store_calibration("calib_model")
        assert isinstance(temperature, float)
        assert 0.05 <= temperature <= 20.0

        _, entry = get_active_model("calib_model")
        assert entry.calibration_temperature == pytest.approx(temperature)

    def test_raises_when_no_active_model(self, temp_db, tmp_path, monkeypatch):
        import core.ml.registry as registry_module

        monkeypatch.setattr(registry_module, "MODEL_ARTIFACT_DIR", tmp_path)
        with pytest.raises(ValueError, match="No active model"):
            fit_and_store_calibration("never_registered_model")
