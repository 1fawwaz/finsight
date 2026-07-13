"""Tests for core.checkpoint: single-row resumption state for the Phase 1 loop."""

from datetime import date

from core.checkpoint import (
    get_checkpoint,
    is_completed,
    mark_completed,
    mark_failed,
    remaining,
    start_stage,
)
from core.database import CheckpointState


def test_get_checkpoint_creates_single_row_on_first_use(db_session):
    state = get_checkpoint(db_session)
    assert state.id == 1
    assert db_session.query(CheckpointState).count() == 1


def test_get_checkpoint_is_idempotent_single_row(db_session):
    get_checkpoint(db_session)
    get_checkpoint(db_session)
    assert db_session.query(CheckpointState).count() == 1


def test_start_stage_sets_stage_and_versions(db_session):
    state = start_stage(db_session, "backfill", dataset_version="dataset_v2", feature_version="features_v2")
    assert state.current_stage == "backfill"
    assert state.current_dataset_version == "dataset_v2"
    assert state.current_feature_version == "features_v2"


def test_start_stage_clears_progress_on_actual_stage_transition(db_session):
    start_stage(db_session, "validation")
    mark_completed(db_session, "FIN-0001")
    assert is_completed(db_session, "FIN-0001")

    start_stage(db_session, "backfill")
    assert is_completed(db_session, "FIN-0001") is False


def test_start_stage_preserves_progress_when_resuming_same_stage(db_session):
    """The critical resumability guarantee: a loop interrupted mid-stage and restarted
    re-calls start_stage for the *same* stage it was already in (per spec §4
    RECONNAISSANCE). That must not wipe out the very progress being resumed."""
    start_stage(db_session, "backfill")
    mark_completed(db_session, "FIN-0001")

    start_stage(db_session, "backfill")  # simulates a fresh session resuming the same stage

    assert is_completed(db_session, "FIN-0001") is True


def test_mark_completed_tracks_last_processed(db_session):
    state = mark_completed(db_session, "FIN-0001", processed_date=date(2026, 7, 10))
    assert state.last_processed_internal_id == "FIN-0001"
    assert state.last_processed_date == date(2026, 7, 10)
    assert is_completed(db_session, "FIN-0001")


def test_mark_completed_is_idempotent_no_duplicates(db_session):
    mark_completed(db_session, "FIN-0001")
    mark_completed(db_session, "FIN-0001")
    state = get_checkpoint(db_session)
    import json

    assert json.loads(state.completed_internal_ids_json).count("FIN-0001") == 1


def test_mark_failed_records_reason_and_timestamp(db_session):
    state = mark_failed(db_session, "FIN-0002", "delisted security")
    import json

    failed = json.loads(state.failed_internal_ids_json)
    assert len(failed) == 1
    assert failed[0]["internal_id"] == "FIN-0002"
    assert failed[0]["reason"] == "delisted security"
    assert "timestamp" in failed[0]


def test_remaining_excludes_completed_units(db_session):
    mark_completed(db_session, "FIN-0001")
    mark_completed(db_session, "FIN-0003")

    left = remaining(db_session, ["FIN-0001", "FIN-0002", "FIN-0003", "FIN-0004"])

    assert left == ["FIN-0002", "FIN-0004"]


def test_resume_after_interruption_skips_completed_work(db_session):
    """Simulates a loop interrupted mid-stage: on resume, RECONNAISSANCE (get_checkpoint)
    + PLAN (remaining) must produce exactly the units not yet done, never restarting from
    zero, per spec §4."""
    start_stage(db_session, "backfill")
    universe = ["FIN-0001", "FIN-0002", "FIN-0003"]

    mark_completed(db_session, "FIN-0001")
    mark_failed(db_session, "FIN-0002", "transient network error, retries exhausted")
    # Simulate interruption here -- a fresh call sequence below stands in for a new
    # session resuming from disk state alone.

    to_process = remaining(db_session, universe)
    assert to_process == ["FIN-0002", "FIN-0003"]  # failed units aren't "completed", so they're still visible to retry/inspect
