"""Phase 1 Step 17: feature store integration -- proves the Parquet-sourced path
(core.ml.feature_pipeline.make_dataset_v2_from_parquet) produces results identical to
the existing SQLite-sourced path for the same underlying data, since both reuse the same
build_features_v2/build_labels unchanged."""

from datetime import date, timedelta

import pandas as pd

from core.database import Price, get_session
from core.ml.feature_pipeline import make_dataset_v2, make_dataset_v2_from_parquet
from core.parquet_store import sync_from_sqlite
from core.symbol_registry import get_or_create


def _seed(session, internal_id_symbol="PARQFEAT.NS", n=60):
    entry = get_or_create(session, internal_id_symbol)
    start = date(2024, 1, 1)
    for i in range(n):
        session.add(
            Price(
                ticker_id=1, internal_id=entry.internal_id, date=start + timedelta(days=i),
                open=100.0 + i * 0.3, high=101.0 + i * 0.3, low=99.0 + i * 0.3, close=100.5 + i * 0.3, volume=1_000_000 + i,
            )
        )
    session.flush()
    return entry.internal_id


def test_parquet_sourced_features_match_sqlite_sourced_features(temp_db, tmp_path, monkeypatch):
    import core.parquet_store as parquet_module
    monkeypatch.setattr(parquet_module, "MARKET_DATA_DIR", tmp_path / "market_data")
    (tmp_path / "market_data").mkdir()

    with get_session() as session:
        internal_id = _seed(session)
        sync_from_sqlite(session, internal_id)
        rows = session.query(Price).filter_by(internal_id=internal_id).order_by(Price.date).all()
        # Matches core.queries.get_price_history's exact convention (the real,
        # production SQLite-reading path): set_index("date") keeps the index named
        # "date", which is also what core.parquet_store.read_market_data produces --
        # not a coincidence to paper over, but the actual shared contract both paths
        # must honor for this test to be a meaningful comparison.
        price_df = pd.DataFrame(
            [{"date": r.date, "open": r.open, "high": r.high, "low": r.low, "close": r.close, "volume": r.volume} for r in rows]
        )
        price_df["date"] = pd.to_datetime(price_df["date"])
        price_df = price_df.set_index("date")

    sqlite_features, sqlite_labels = make_dataset_v2(price_df)
    parquet_features, parquet_labels = make_dataset_v2_from_parquet(internal_id)

    pd.testing.assert_frame_equal(sqlite_features, parquet_features, check_freq=False)
    pd.testing.assert_series_equal(sqlite_labels, parquet_labels, check_freq=False)


def test_make_dataset_v2_from_parquet_requires_prior_sync(temp_db, tmp_path, monkeypatch):
    """Reading a symbol that was never synced to Parquet returns an empty dataset
    (via core.parquet_store.read_market_data's own empty-DataFrame contract), not a
    crash -- SQLite being the source of truth means an un-synced symbol just has no
    Parquet data yet, which is a legitimate, handled state."""
    import core.parquet_store as parquet_module
    monkeypatch.setattr(parquet_module, "MARKET_DATA_DIR", tmp_path / "market_data")
    (tmp_path / "market_data").mkdir()

    features, labels = make_dataset_v2_from_parquet("FIN-NEVER-SYNCED")

    assert len(features) == 0
    assert len(labels) == 0
