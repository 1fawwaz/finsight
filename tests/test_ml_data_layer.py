"""Tests for core.ml.data_layer: dataset versioning and quality validation."""

import json
from datetime import date, timedelta

import pandas as pd
import pytest

from core.data_ingestion import IngestionError
from core.database import MLDatasetVersion, Price, Ticker, get_session
from core.ml.data_layer import (
    MIN_ROWS_FOR_TRAINING,
    create_dataset_version,
    get_dataset_version,
    load_dataset,
    sync_universe,
    validate_symbol_history,
)


def _make_history(n: int, start: str = "2024-01-01", bad_range_row: bool = False, outlier_row: bool = False) -> pd.DataFrame:
    dates = pd.bdate_range(start=start, periods=n)
    close = [100.0 + i * 0.1 for i in range(n)]
    df = pd.DataFrame(
        {
            "open": [c - 0.5 for c in close],
            "high": [c + 1.0 for c in close],
            "low": [c - 1.0 for c in close],
            "close": close,
            "volume": [1_000_000] * n,
        },
        index=dates,
    )
    if bad_range_row and n > 0:
        df.iloc[0, df.columns.get_loc("high")] = df.iloc[0]["low"] - 5.0  # high < low
    if outlier_row and n > 1:
        df.iloc[1, df.columns.get_loc("close")] = df.iloc[0]["close"] * 2.0  # >40% single-day jump
    return df


def test_validate_symbol_history_clean_data_passes_all_checks():
    history = _make_history(600)
    report = validate_symbol_history("CLEAN.NS", history)
    assert report.schema_valid is True
    assert report.duplicate_dates == 0
    assert report.out_of_order_timestamps == 0
    assert report.missing_value_rows == 0
    assert report.range_violations == []
    assert report.included_in_dataset is True
    assert report.exclusion_reason is None


def test_validate_symbol_history_flags_range_violations():
    history = _make_history(600, bad_range_row=True)
    report = validate_symbol_history("BADRANGE.NS", history)
    assert any("high < low" in v for v in report.range_violations)


def test_validate_symbol_history_flags_outlier_days():
    # A single-day price-level shift shows up as two flagged return transitions: the
    # jump itself, and the reversion back to trend on the following day.
    history = _make_history(600, outlier_row=True)
    report = validate_symbol_history("OUTLIER.NS", history)
    assert len(report.outlier_days) == 2


def test_validate_symbol_history_excludes_short_history():
    history = _make_history(MIN_ROWS_FOR_TRAINING - 1)
    report = validate_symbol_history("SHORT.NS", history)
    assert report.included_in_dataset is False
    assert "rows" in report.exclusion_reason


def test_validate_symbol_history_includes_at_exactly_the_minimum():
    history = _make_history(MIN_ROWS_FOR_TRAINING)
    report = validate_symbol_history("EXACT.NS", history)
    assert report.included_in_dataset is True


def test_validate_symbol_history_empty_is_excluded_not_crash():
    report = validate_symbol_history("EMPTY.NS", pd.DataFrame(columns=["open", "high", "low", "close", "volume"]))
    assert report.included_in_dataset is False
    assert report.exclusion_reason == "empty history"


def test_validate_symbol_history_missing_columns_is_excluded():
    bad_df = pd.DataFrame({"close": [100.0, 101.0]}, index=pd.bdate_range("2024-01-01", periods=2))
    report = validate_symbol_history("MISSINGCOLS.NS", bad_df)
    assert report.schema_valid is False
    assert report.included_in_dataset is False


def _seed_ticker_with_history(session, symbol: str, n: int, sector: str = "Technology") -> None:
    ticker = Ticker(symbol=symbol, name=symbol, sector=sector)
    session.add(ticker)
    session.flush()
    start = date(2024, 1, 1)
    for i in range(n):
        session.add(
            Price(
                ticker_id=ticker.id,
                date=start + timedelta(days=i),
                open=100.0 + i * 0.1,
                high=101.0 + i * 0.1,
                low=99.0 + i * 0.1,
                close=100.5 + i * 0.1,
                volume=1_000_000,
            )
        )


def test_create_dataset_version_excludes_short_history_symbol_but_keeps_the_rest(temp_db):
    with get_session() as session:
        _seed_ticker_with_history(session, "GOOD.NS", 600)
        _seed_ticker_with_history(session, "TOOSHORT.NS", 50)

    version = create_dataset_version(["GOOD.NS", "TOOSHORT.NS"], version_name="test_v1")

    assert version.symbol_count == 1
    quality = json.loads(version.quality_report_json)
    assert "GOOD.NS" in quality["included_symbols"]
    assert "TOOSHORT.NS" in quality["excluded_symbols"]


def test_create_dataset_version_raises_if_all_symbols_excluded(temp_db):
    with get_session() as session:
        _seed_ticker_with_history(session, "TOOSHORT.NS", 10)

    with pytest.raises(ValueError, match="No symbols passed"):
        create_dataset_version(["TOOSHORT.NS"], version_name="test_v_empty")


def test_create_dataset_version_persists_retrievable_metadata(temp_db):
    with get_session() as session:
        _seed_ticker_with_history(session, "GOOD.NS", 600)

    created = create_dataset_version(["GOOD.NS"], version_name="test_v2")
    fetched = get_dataset_version("test_v2")

    assert fetched is not None
    assert fetched.version == created.version
    assert fetched.row_count == created.row_count
    assert fetched.symbol_count == 1


def test_create_dataset_version_stamps_internal_id_per_symbol(temp_db):
    """Phase 1: every symbol report in the persisted quality JSON carries its permanent
    internal_id, and the aggregate included_internal_ids list is derivable from it --
    spec §7.9's "internal_id set covered" requirement."""
    with get_session() as session:
        _seed_ticker_with_history(session, "GOOD.NS", 600)

    version = create_dataset_version(["GOOD.NS"], version_name="test_v_internal_id")

    quality = json.loads(version.quality_report_json)
    good_report = next(r for r in quality["symbol_reports"] if r["symbol"] == "GOOD.NS")
    assert good_report["internal_id"] is not None
    assert good_report["internal_id"].startswith("FIN-")
    assert good_report["internal_id"] in quality["included_internal_ids"]


def test_create_dataset_version_states_constituent_history_is_unavailable(temp_db):
    """Phase 1: rather than silently omitting point-in-time constituent history (spec
    §7.6, blocked pending an authoritative Nifty index-membership source), the manifest
    JSON says so explicitly -- a reader must never assume this dataset version is
    survivorship-bias-safe just because the field exists and looks populated."""
    with get_session() as session:
        _seed_ticker_with_history(session, "GOOD.NS", 600)

    version = create_dataset_version(["GOOD.NS"], version_name="test_v_constituent_note")

    quality = json.loads(version.quality_report_json)
    assert "not_available" in quality["constituent_history"]


def test_load_dataset_reproduces_the_same_rows(temp_db):
    with get_session() as session:
        _seed_ticker_with_history(session, "GOOD.NS", 600)

    version = create_dataset_version(["GOOD.NS"], version_name="test_v3")
    loaded = load_dataset(version.version)

    assert set(loaded.keys()) == {"GOOD.NS"}
    assert len(loaded["GOOD.NS"]) == 600


def test_load_dataset_unknown_version_raises(temp_db):
    with pytest.raises(ValueError, match="No dataset version"):
        load_dataset("does_not_exist")


def test_sync_universe_retries_transient_failure_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def _flaky_ingest(symbol, period="5y"):
        calls["n"] += 1
        if calls["n"] < 3:
            raise IngestionError("simulated transient network failure")
        return 42

    monkeypatch.setattr("core.ml.data_layer.ingest_ticker", _flaky_ingest)
    monkeypatch.setattr("core.ml.data_layer.time.sleep", lambda seconds: None)  # no real delay in tests

    result = sync_universe(["FLAKY.NS"])
    assert result == {"FLAKY.NS": 42}
    assert calls["n"] == 3  # failed twice, succeeded on the 3rd attempt


def test_sync_universe_gives_up_after_max_attempts_and_logs_not_raises(monkeypatch):
    calls = {"n": 0}

    def _always_fails(symbol, period="5y"):
        calls["n"] += 1
        raise IngestionError("permanently broken symbol")

    monkeypatch.setattr("core.ml.data_layer.ingest_ticker", _always_fails)
    monkeypatch.setattr("core.ml.data_layer.time.sleep", lambda seconds: None)

    result = sync_universe(["BROKEN.NS"])
    assert result == {"BROKEN.NS": 0}  # skipped, not raised
    assert calls["n"] == 3  # exactly max_attempts, no more
