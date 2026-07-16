"""Phase 1 Checkpoint System: single-row resumption state for the autonomous loop.

Persists exactly what spec §7.16 requires: completed symbols (by `internal_id`), failed
symbols, last processed date/symbol, current stage, current dataset/feature version.
Single-row by design -- see `core.database.CheckpointState` and
`docs/SCHEMA.md`"checkpoint_state" for the full rationale (one continuously-resumed
process, not concurrent independent runs).
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

from core.config import get_logger
from core.database import CheckpointState

logger = get_logger(__name__)

_CHECKPOINT_ID = 1


def get_checkpoint(session) -> CheckpointState:
    """The single checkpoint row, creating a fresh (empty) one on first use."""
    state = session.get(CheckpointState, _CHECKPOINT_ID)
    if state is None:
        state = CheckpointState(
            id=_CHECKPOINT_ID,
            completed_internal_ids_json="[]",
            failed_internal_ids_json="[]",
        )
        session.add(state)
        session.flush()
    return state


def start_stage(session, stage: str, dataset_version: str | None = None, feature_version: str | None = None) -> CheckpointState:
    """Enter `stage`. Clears the completed/failed lists only on an actual *transition*
    to a different stage -- per-stage progress, not a lifetime accumulation across
    unrelated stages (e.g. "symbols completed during validation" shouldn't linger once
    ingestion starts). Critically, re-entering the *same* stage (the normal case when a
    resumed loop's RECONNAISSANCE step re-calls this, per spec §4) is a no-op on the
    progress lists -- otherwise every resume would erase the very progress checkpointing
    exists to preserve.
    """
    state = get_checkpoint(session)
    if state.current_stage != stage:
        state.completed_internal_ids_json = "[]"
        state.failed_internal_ids_json = "[]"
        logger.info("Checkpoint: transitioning stage %r -> %r (progress reset)", state.current_stage, stage)
    else:
        logger.info("Checkpoint: resuming stage=%r (progress preserved)", stage)
    state.current_stage = stage
    if dataset_version is not None:
        state.current_dataset_version = dataset_version
    if feature_version is not None:
        state.current_feature_version = feature_version
    session.flush()
    return state


def mark_completed(session, internal_id: str, processed_date: date | None = None) -> CheckpointState:
    """Record one unit of work as done. Idempotent -- marking the same internal_id
    complete twice does not duplicate it in the list, so a resumed loop that re-processes
    its last (possibly partially-done) unit doesn't corrupt the record."""
    state = get_checkpoint(session)
    completed = json.loads(state.completed_internal_ids_json)
    if internal_id not in completed:
        completed.append(internal_id)
    state.completed_internal_ids_json = json.dumps(completed)
    state.last_processed_internal_id = internal_id
    if processed_date is not None:
        state.last_processed_date = processed_date
    session.flush()
    return state


def mark_failed(session, internal_id: str, reason: str) -> CheckpointState:
    """Record one unit of work as failed (non-recoverable, per spec §11) -- logged and
    skipped, not silently dropped, so the next RECONNAISSANCE pass can see exactly what
    was skipped and why."""
    state = get_checkpoint(session)
    failed = json.loads(state.failed_internal_ids_json)
    failed.append({"internal_id": internal_id, "reason": reason, "timestamp": datetime.now(timezone.utc).isoformat()})
    state.failed_internal_ids_json = json.dumps(failed)
    session.flush()
    logger.warning("Checkpoint: marked %s failed -- %s", internal_id, reason)
    return state


def is_completed(session, internal_id: str) -> bool:
    """True if `internal_id` is already marked done in the current stage -- the
    RECONNAISSANCE-time check that makes a resumed loop skip already-finished work
    instead of restarting from zero."""
    state = get_checkpoint(session)
    return internal_id in json.loads(state.completed_internal_ids_json)


def remaining(session, all_internal_ids: list[str]) -> list[str]:
    """`all_internal_ids` minus whatever the checkpoint already marks completed --
    the smallest next unit of work (spec §4 step 2, PLAN)."""
    state = get_checkpoint(session)
    completed = set(json.loads(state.completed_internal_ids_json))
    return [i for i in all_internal_ids if i not in completed]
