"""Phase 3 Feature Pipeline: an extended, versioned feature set persisted to a SQLite
feature store.

Reuses core.ml_model.build_labels (the target definition) and core.indicators (every
indicator computation) rather than reimplementing anything -- this module adds features
core.ml_model.build_features didn't already compute (ATR, ADX, VWAP, Bollinger,
SMA/EMA distance, ROC, momentum, volume ratio, gap %, candle anatomy, support/resistance
distance, 52-week range distance), plus the feature-store persistence layer itself.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from datetime import date

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core.config import get_logger
from core.database import MLFeatureSet, MLFeatureValue, Ticker, get_session
from core.indicators import adx, atr, bollinger_bands, ema, macd, rsi, sma, support_resistance, volatility, vwap
from core.ml_model import build_labels

logger = get_logger(__name__)


def build_features_v2(price_df: pd.DataFrame, sentiment_by_date: pd.Series | None = None) -> pd.DataFrame:
    """Extended feature set (27 features, 28 with sentiment): the original 9 from
    core.ml_model.build_features
    (kept numerically identical, computed the same way, so results on those columns are
    directly comparable across pipeline versions) plus ATR, ADX, VWAP, Bollinger %B/
    bandwidth, SMA/EMA distance, ROC, momentum, volume ratio, gap %, candle-anatomy
    ratios, support/resistance distance, and 52-week-range distance.

    `price_df` must have open/high/low/close/volume columns, date-indexed, ascending.
    """
    close = price_df["close"]
    high = price_df["high"]
    low = price_df["low"]
    open_ = price_df["open"]
    volume = price_df["volume"]

    features = pd.DataFrame(index=price_df.index)

    # --- Original 9 (unchanged from core.ml_model.build_features) ---
    features["lag_return_1"] = close.pct_change(1)
    features["lag_return_2"] = close.pct_change(2)
    features["lag_return_3"] = close.pct_change(3)
    features["lag_return_5"] = close.pct_change(5)
    volume_mean = volume.rolling(window=20, min_periods=20).mean()
    volume_std = volume.rolling(window=20, min_periods=20).std()
    features["volume_zscore"] = (volume - volume_mean) / volume_std
    features["rsi_14"] = rsi(close, window=14)
    macd_df = macd(close)
    features["macd"] = macd_df["macd"]
    features["macd_signal"] = macd_df["signal"]
    features["volatility_20"] = volatility(close, window=20, annualize=True)

    # --- Extensions ---
    features["atr_14"] = atr(high, low, close, window=14)
    features["adx_14"] = adx(high, low, close, window=14)
    vwap_20 = vwap(high, low, close, volume, window=20)
    features["price_to_vwap"] = close / vwap_20 - 1

    bands = bollinger_bands(close, window=20, num_std=2.0)
    band_width = bands["upper"] - bands["lower"]
    features["bollinger_pct_b"] = (close - bands["lower"]) / band_width
    features["bollinger_bandwidth"] = band_width / close

    features["sma_20_dist"] = close / sma(close, window=20) - 1
    features["ema_20_dist"] = close / ema(close, span=20) - 1

    features["roc_10"] = close.pct_change(10)
    features["momentum_10"] = close - close.shift(10)
    features["volume_ratio_5_20"] = volume.rolling(5).mean() / volume.rolling(20).mean()

    prev_close = close.shift(1)
    features["gap_pct"] = (open_ - prev_close) / prev_close
    candle_range = (high - low).where((high - low) != 0)
    body_high = pd.concat([open_, close], axis=1).max(axis=1)
    body_low = pd.concat([open_, close], axis=1).min(axis=1)
    features["candle_body_pct"] = (close - open_).abs() / candle_range
    features["upper_wick_pct"] = (high - body_high) / candle_range
    features["lower_wick_pct"] = (body_low - low) / candle_range

    sr = support_resistance(high, low, window=20)
    features["dist_from_support"] = close / sr["support"] - 1
    features["dist_from_resistance"] = close / sr["resistance"] - 1

    rolling_high_252 = high.rolling(window=252, min_periods=60).max()
    rolling_low_252 = low.rolling(window=252, min_periods=60).min()
    features["dist_from_52w_high"] = close / rolling_high_252 - 1
    features["dist_from_52w_low"] = close / rolling_low_252 - 1

    if sentiment_by_date is not None:
        features["sentiment"] = sentiment_by_date.reindex(features.index).fillna(0.0)

    return features


def make_dataset_v2(
    price_df: pd.DataFrame, sentiment_by_date: pd.Series | None = None
) -> tuple[pd.DataFrame, pd.Series]:
    """Clean, aligned (features, labels) using the extended feature set. Same no-lookahead
    contract as core.ml_model.make_dataset: label at row t is the direction from t to
    t+1, so the last row (undefined label) is always dropped."""
    features = build_features_v2(price_df, sentiment_by_date)
    labels = build_labels(price_df["close"])
    combined = features.join(labels.rename("label")).dropna()
    return combined.drop(columns=["label"]), combined["label"].astype(int)


def make_dataset_v2_from_parquet(
    internal_id: str, sentiment_by_date: pd.Series | None = None
) -> tuple[pd.DataFrame, pd.Series]:
    """Phase 1 Step 17: identical to `make_dataset_v2`, sourced from the Parquet
    market_data store (core.parquet_store, Step 16) instead of SQLite -- reuses
    `build_features_v2`/`build_labels` unchanged, so results are directly comparable to
    the SQLite-sourced path; only the read path differs (faster, columnar, per spec
    §7.14's stated benefit). Requires `core.parquet_store.sync_from_sqlite(internal_id)`
    to have been run at least once -- SQLite remains the source of truth this reads a
    synced copy of, not an independent one.
    """
    from core.parquet_store import read_market_data  # local import: avoids a module-level cycle (parquet_store doesn't need feature_pipeline)

    price_df = read_market_data(internal_id)
    return make_dataset_v2(price_df, sentiment_by_date)


def _pipeline_code_hash() -> str:
    """Hash of this module's feature-generation source, so a stored feature set can be
    tied to the exact code that produced it -- if build_features_v2 changes, its hash
    changes, and old feature-store rows are recognizably from a prior code version."""
    source = inspect.getsource(build_features_v2)
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]


def _next_feature_version() -> str:
    with get_session() as session:
        count = len(session.execute(select(MLFeatureSet)).scalars().all())
        return f"features_v{count + 1}"


def persist_feature_set(
    dataset_version: str,
    features_by_symbol: dict[str, pd.DataFrame],
    labels_by_symbol: dict[str, pd.Series],
    feature_version: str | None = None,
) -> MLFeatureSet:
    """Persist an already-computed (features, labels) set per symbol to the SQLite
    feature store, with metadata tying it back to the dataset version and the exact
    generation code that produced it."""
    all_columns: set[str] = set()
    for df in features_by_symbol.values():
        all_columns.update(df.columns)
    feature_names = sorted(all_columns)

    version = feature_version or _next_feature_version()
    code_hash = _pipeline_code_hash()
    total_rows = sum(len(df) for df in features_by_symbol.values())

    with get_session() as session:
        feature_set = MLFeatureSet(
            feature_version=version,
            dataset_version=dataset_version,
            feature_names_json=json.dumps(feature_names),
            pipeline_code_hash=code_hash,
            row_count=total_rows,
        )
        session.add(feature_set)
        session.flush()

        for symbol, feat_df in features_by_symbol.items():
            ticker = session.execute(select(Ticker).where(Ticker.symbol == symbol)).scalar_one_or_none()
            if ticker is None:
                logger.warning("Feature store: skipping %s -- no Ticker row found", symbol)
                continue
            labels = labels_by_symbol.get(symbol)
            rows = []
            for ts, row in feat_df.iterrows():
                bar_date: date = ts.date() if hasattr(ts, "date") else ts
                label_value = None
                if labels is not None and ts in labels.index:
                    label_value = int(labels.loc[ts])
                rows.append(
                    {
                        "feature_set_id": feature_set.id,
                        "ticker_id": ticker.id,
                        "date": bar_date,
                        "features_json": json.dumps(row.to_dict(), default=float),
                        "label": label_value,
                    }
                )
            if rows:
                session.execute(sqlite_insert(MLFeatureValue).values(rows).on_conflict_do_nothing())

        session.flush()
        logger.info(
            "Persisted feature set %s: %d features, %d rows across %d symbols (dataset %s, code hash %s)",
            version,
            len(feature_names),
            total_rows,
            len(features_by_symbol),
            dataset_version,
            code_hash,
        )
        return feature_set


def load_feature_set(feature_version: str) -> tuple[pd.DataFrame, pd.Series]:
    """Reload a persisted feature set from the SQLite feature store rather than
    recomputing it -- returns a combined (features, labels) DataFrame/Series across all
    symbols in that feature set, indexed by (symbol, date)."""
    with get_session() as session:
        feature_set = session.execute(
            select(MLFeatureSet).where(MLFeatureSet.feature_version == feature_version)
        ).scalar_one_or_none()
        if feature_set is None:
            raise ValueError(f"No feature set named {feature_version!r} in the feature store.")

        rows = session.execute(
            select(MLFeatureValue, Ticker.symbol)
            .join(Ticker, MLFeatureValue.ticker_id == Ticker.id)
            .where(MLFeatureValue.feature_set_id == feature_set.id)
            .order_by(Ticker.symbol, MLFeatureValue.date)
        ).all()

    records = []
    labels = []
    index = []
    for value, symbol in rows:
        feature_dict = json.loads(value.features_json)
        records.append(feature_dict)
        labels.append(value.label)
        index.append((symbol, value.date))

    features_df = pd.DataFrame(records, index=pd.MultiIndex.from_tuples(index, names=["symbol", "date"]))
    labels_series = pd.Series(labels, index=features_df.index, name="label")
    # Rows with no label (the always-undefined last row per symbol) are excluded --
    # they were stored for completeness but were never meant to be trained on.
    has_label = labels_series.notna()
    return features_df[has_label], labels_series[has_label].astype(int)
