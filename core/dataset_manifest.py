"""Phase 1 Step 13: Dataset Manifest generation -- spec §7.10's exact JSON schema,
generated automatically from a dataset version, never hand-written.

Reuses `core.ml.data_layer.get_dataset_version` (Phase 3) rather than re-querying
`MLDatasetVersion` directly, and `core.metadata_registry`/`core.corporate_actions`
(Steps 11/8) for the Data Quality Score components -- no new source of truth, just an
aggregation layer over what earlier Phase 1 steps already compute and persist.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone

import yfinance

from core.config import BASE_DIR, get_logger
from core.corporate_actions import validate_corporate_action_consistency
from core.database import MetadataRegistry, get_session
from core.market_status import has_holiday_data
from core.ml.data_layer import get_dataset_version

logger = get_logger(__name__)

MANIFEST_DIR = BASE_DIR / "data" / "manifests"
MANIFEST_DIR.mkdir(parents=True, exist_ok=True)

# Decided in docs/DATA_SOURCE.md §6: internal_id (not raw ticker) as the outer partition
# key, so a rename never splits a partition; year as the inner key for per-symbol
# backtest locality. Recorded here, not re-derived, so the manifest and the docs can
# never silently drift apart on this.
PARTITION_SCHEME = "internal_id/year"


@dataclass
class QualityScore:
    completeness: float
    integrity: float
    coverage: float
    freshness: float
    corporate_action_validation: float
    composite: float


def _dataset_checksum(version: str, start_date: date, end_date: date, row_count: int, internal_ids: list[str]) -> str:
    """A dataset version is a *pointer* (core.ml.data_layer's own docstring), not a data
    copy -- so its checksum is a reproducible fingerprint of that pointer's own defining
    fields, not a hash of the underlying price data itself (which `prices` already
    guarantees integrity for via its own constraints)."""
    payload = json.dumps(
        {"version": version, "start_date": start_date.isoformat(), "end_date": end_date.isoformat(), "row_count": row_count, "internal_ids": sorted(internal_ids)},
        sort_keys=True,
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _compute_quality_score(session, symbol_reports: list[dict], internal_ids: list[str]) -> QualityScore:
    included = [r for r in symbol_reports if r["included_in_dataset"]]
    total_symbols = len(symbol_reports)

    completeness = (
        100.0 * sum(1 - (r["missing_value_rows"] / r["row_count"] if r["row_count"] else 0) for r in included) / len(included)
        if included else 0.0
    )
    integrity = (
        100.0 * sum(1 for r in included if not r["range_violations"] and r["duplicate_dates"] == 0) / len(included)
        if included else 0.0
    )
    coverage = 100.0 * len(included) / total_symbols if total_symbols else 0.0

    today = datetime.now(timezone.utc).date()
    freshness_scores = []
    for internal_id in internal_ids:
        entry = session.get(MetadataRegistry, internal_id)
        if entry is None or entry.latest_date is None:
            continue
        staleness_days = (today - entry.latest_date).days
        # Full marks within 3 calendar days of "now" (covers weekends); degrades
        # linearly after that rather than a hard cliff, floored at 0.
        freshness_scores.append(max(0.0, 100.0 - max(0, staleness_days - 3) * 5.0))
    freshness = sum(freshness_scores) / len(freshness_scores) if freshness_scores else 0.0

    ca_scores = [100.0 if validate_corporate_action_consistency(session, i).passed else 0.0 for i in internal_ids]
    corporate_action_validation = sum(ca_scores) / len(ca_scores) if ca_scores else 100.0

    composite = (completeness + integrity + coverage + freshness + corporate_action_validation) / 5
    return QualityScore(
        completeness=round(completeness, 2),
        integrity=round(integrity, 2),
        coverage=round(coverage, 2),
        freshness=round(freshness, 2),
        corporate_action_validation=round(corporate_action_validation, 2),
        composite=round(composite, 2),
    )


def generate_manifest(dataset_version: str) -> dict:
    """Build and persist `data/manifests/{dataset_version}_manifest.json`. Manifests are
    immutable once written, same as the dataset version they describe -- regenerating
    the same version overwrites its own manifest (idempotent for the same input) but a
    correction always produces a new dataset version + new manifest, never an edit to a
    prior one's underlying facts.
    """
    record = get_dataset_version(dataset_version)
    if record is None:
        raise ValueError(f"No dataset version named {dataset_version!r} -- generate it first via create_dataset_version.")

    quality_report = json.loads(record.quality_report_json)
    internal_ids = quality_report.get("included_internal_ids", [])

    with get_session() as session:
        quality_score = _compute_quality_score(session, quality_report["symbol_reports"], internal_ids)

    manifest = {
        "dataset_version": record.version,
        "checksum": _dataset_checksum(record.version, record.start_date, record.end_date, record.row_count, internal_ids),
        "created": datetime.now(timezone.utc).isoformat(),
        "symbols": {"count": record.symbol_count, "internal_ids": internal_ids},
        "date_range": {"start": record.start_date.isoformat(), "end": record.end_date.isoformat()},
        "provider_versions": {
            "yfinance": getattr(yfinance, "__version__", "unknown"),
            # Honest, not a fabricated "official edition" string: the calendar is a
            # hand-maintained table (core.market_status), only covering the years
            # listed there -- see docs/DATA_SOURCE.md's "Partially Active" note.
            "nse_calendar": "hand-maintained (" + ", ".join(str(y) for y in range(2020, 2031) if has_holiday_data(y)) + ")",
        },
        "feature_version": None,  # set by the feature-store integration (Step 17) when this dataset version is used for training
        "quality_score": asdict(quality_score),
        "row_count": record.row_count,
        "partition_scheme": PARTITION_SCHEME,
    }

    manifest_path = MANIFEST_DIR / f"{dataset_version}_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    logger.info("Generated dataset manifest: %s (composite quality score=%.2f)", manifest_path, quality_score.composite)
    return manifest


def load_manifest(dataset_version: str) -> dict:
    manifest_path = MANIFEST_DIR / f"{dataset_version}_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"No manifest found for dataset version {dataset_version!r} at {manifest_path}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))
