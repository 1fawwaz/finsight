"""Tests for core.dataset_manifest: spec §7.10's manifest schema, generated from a
real dataset version rather than hand-written."""

import json
from datetime import date, timedelta

import pytest

import core.dataset_manifest as manifest_module
from core.database import Price, SymbolRegistry, Ticker, get_session
from core.dataset_manifest import generate_manifest, load_manifest
from core.metadata_registry import refresh_metadata
from core.ml.data_layer import create_dataset_version
from core.symbol_registry import get_or_create


def _seed_symbol(session, symbol: str, n: int, latest_date: date | None = None):
    ticker = Ticker(symbol=symbol, name=symbol, sector="Technology")
    session.add(ticker)
    session.flush()
    entry = get_or_create(session, symbol)  # resolved first, so Price rows can be stamped like real ingestion does
    end = latest_date or date.today()
    start = end - timedelta(days=n - 1)
    for i in range(n):
        session.add(
            Price(
                ticker_id=ticker.id,
                internal_id=entry.internal_id,
                date=start + timedelta(days=i),
                open=100.0, high=101.0, low=99.0, close=100.5, volume=1_000_000,
            )
        )
    session.flush()
    return entry.internal_id


@pytest.fixture()
def patched_manifest_dir(tmp_path, monkeypatch):
    manifest_dir = tmp_path / "manifests"
    manifest_dir.mkdir()
    monkeypatch.setattr(manifest_module, "MANIFEST_DIR", manifest_dir)
    return manifest_dir


def test_generate_manifest_writes_a_json_file(temp_db, patched_manifest_dir):
    with get_session() as session:
        internal_id = _seed_symbol(session, "GOOD.NS", 600)
        refresh_metadata(session, internal_id)

    create_dataset_version(["GOOD.NS"], version_name="test_manifest_v1")
    manifest = generate_manifest("test_manifest_v1")

    manifest_path = patched_manifest_dir / "test_manifest_v1_manifest.json"
    assert manifest_path.exists()
    on_disk = json.loads(manifest_path.read_text())
    assert on_disk == manifest


def test_manifest_has_every_required_field(temp_db, patched_manifest_dir):
    with get_session() as session:
        internal_id = _seed_symbol(session, "GOOD.NS", 600)
        refresh_metadata(session, internal_id)

    create_dataset_version(["GOOD.NS"], version_name="test_manifest_v2")
    manifest = generate_manifest("test_manifest_v2")

    for key in ("dataset_version", "checksum", "created", "symbols", "date_range", "provider_versions", "feature_version", "quality_score", "row_count", "partition_scheme"):
        assert key in manifest, f"missing required field: {key}"
    assert manifest["symbols"]["count"] == 1
    assert manifest["partition_scheme"] == "internal_id/year"
    assert manifest["checksum"].startswith("sha256:")


def test_quality_score_components_are_bounded_0_to_100(temp_db, patched_manifest_dir):
    with get_session() as session:
        internal_id = _seed_symbol(session, "GOOD.NS", 600)
        refresh_metadata(session, internal_id)

    create_dataset_version(["GOOD.NS"], version_name="test_manifest_v3")
    manifest = generate_manifest("test_manifest_v3")

    for component, value in manifest["quality_score"].items():
        assert 0.0 <= value <= 100.0, f"{component}={value} out of [0, 100]"


def test_freshness_is_high_for_recently_synced_symbol(temp_db, patched_manifest_dir):
    with get_session() as session:
        internal_id = _seed_symbol(session, "GOOD.NS", 600, latest_date=date.today())
        refresh_metadata(session, internal_id)

    create_dataset_version(["GOOD.NS"], version_name="test_manifest_fresh")
    manifest = generate_manifest("test_manifest_fresh")

    assert manifest["quality_score"]["freshness"] >= 95.0


def test_freshness_is_low_for_stale_symbol(temp_db, patched_manifest_dir):
    with get_session() as session:
        stale_date = date.today() - timedelta(days=60)
        internal_id = _seed_symbol(session, "STALE.NS", 600, latest_date=stale_date)
        refresh_metadata(session, internal_id)

    create_dataset_version(["STALE.NS"], version_name="test_manifest_stale")
    manifest = generate_manifest("test_manifest_stale")

    assert manifest["quality_score"]["freshness"] < 50.0


def test_checksum_is_reproducible_for_the_same_version(temp_db, patched_manifest_dir):
    with get_session() as session:
        internal_id = _seed_symbol(session, "GOOD.NS", 600)
        refresh_metadata(session, internal_id)

    create_dataset_version(["GOOD.NS"], version_name="test_manifest_checksum")
    first = generate_manifest("test_manifest_checksum")
    second = generate_manifest("test_manifest_checksum")

    assert first["checksum"] == second["checksum"]


def test_generate_manifest_raises_for_unknown_version(temp_db, patched_manifest_dir):
    with pytest.raises(ValueError, match="No dataset version"):
        generate_manifest("does_not_exist_v99")


def test_load_manifest_reads_back_what_was_written(temp_db, patched_manifest_dir):
    with get_session() as session:
        internal_id = _seed_symbol(session, "GOOD.NS", 600)
        refresh_metadata(session, internal_id)

    create_dataset_version(["GOOD.NS"], version_name="test_manifest_load")
    generate_manifest("test_manifest_load")

    loaded = load_manifest("test_manifest_load")
    assert loaded["dataset_version"] == "test_manifest_load"


def test_load_manifest_missing_raises_file_not_found(patched_manifest_dir):
    with pytest.raises(FileNotFoundError):
        load_manifest("never_generated")
