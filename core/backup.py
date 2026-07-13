"""Timestamped SQLite backups, taken before any schema change or bulk write.

The file-copy primitive (`create_backup`/`verify_backup`/`restore_backup`) was pulled
forward into Step 2 because the operating spec's own Prime Directive -- "never write to
the database without a fresh backup in place first" -- had to be satisfiable before
Step 1 (Symbol Registry) could touch the real database at all. This module now also
completes Step 15: `backup_log` persistence (every backup taken, and whether it was ever
used to restore) and a rollback helper (`restore_last_verified_backup`).
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from core.config import DATA_DIR, DATABASE_URL, get_logger
from core.database import BackupLog, get_session

logger = get_logger(__name__)

BACKUP_DIR = DATA_DIR / "backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

_SQLITE_PREFIX = "sqlite:///"

# Closed enum, matching docs/SCHEMA.md's backup_log.trigger.
BACKUP_TRIGGERS = ("schema_migration", "bulk_ingestion")


def _db_path() -> Path | None:
    """The on-disk path of the current SQLite DB, or None if DATABASE_URL isn't SQLite
    (e.g. a future PostgreSQL DATABASE_URL) -- backups are a SQLite-file-copy operation
    and don't apply to a server-based engine the same way."""
    if not DATABASE_URL.startswith(_SQLITE_PREFIX):
        return None
    return Path(DATABASE_URL[len(_SQLITE_PREFIX):])


def create_backup(reason: str, trigger: str = "bulk_ingestion") -> Path | None:
    """Copy the live DB file to a timestamped backup, verified readable before returning,
    and log the attempt to `backup_log` (Step 15) regardless of outcome.

    `reason` is a free-text description (used in the filename, e.g.
    "phase1_symbol_registry_schema_migration"); `trigger` is the closed-enum category
    (`BACKUP_TRIGGERS`) that classifies *why* per docs/SCHEMA.md's `backup_log.trigger`.

    Returns None (logged, not raised) if there is no DB file yet to back up -- a fresh
    install with no `finsight.db` created yet has nothing to protect. Raises if a DB file
    exists but the resulting backup fails to verify, since silently proceeding past a
    corrupt/incomplete backup would violate the "never write without a fresh backup in
    place" rule in spirit even though a file was technically copied.
    """
    if trigger not in BACKUP_TRIGGERS:
        raise ValueError(f"trigger {trigger!r} is not in the closed enum {BACKUP_TRIGGERS}")

    src = _db_path()
    if src is None or not src.exists():
        logger.info("No existing SQLite DB at %s -- nothing to back up before this write.", src)
        return None

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_reason = "".join(c if c.isalnum() or c in "-_" else "_" for c in reason)
    dest = BACKUP_DIR / f"finsight_{safe_reason}_{timestamp}.db"
    shutil.copy2(src, dest)

    verified = verify_backup(dest)
    if not verified:
        dest.unlink(missing_ok=True)
        _log_backup(trigger, dest.name, verified=False)
        raise RuntimeError(f"Backup verification failed for {dest} -- backup removed, not left in a false-good state.")

    _log_backup(trigger, dest.name, verified=True)
    logger.info("Backup created and verified: %s (reason=%s, trigger=%s)", dest, reason, trigger)
    return dest


def _log_backup(trigger: str, backup_filename: str, verified: bool) -> None:
    """Best-effort backup_log write -- a logging failure must never be mistaken for the
    backup itself having failed (the file-copy + verify above already happened)."""
    try:
        with get_session() as session:
            session.add(BackupLog(trigger=trigger, backup_path=backup_filename, verified=verified))
    except Exception as exc:
        logger.warning("backup_log write failed (backup file itself is unaffected): %s", exc)


def verify_backup(path: Path) -> bool:
    """True if `path` is a readable, structurally intact SQLite DB (PRAGMA integrity_check)."""
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()
            return result is not None and result[0] == "ok"
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning("Backup verification failed for %s: %s", path, exc)
        return False


def restore_backup(path: Path) -> None:
    """Restore `path` over the live DB. Verifies `path` before overwriting anything --
    never restores from a backup that doesn't itself pass integrity_check. Marks the
    matching `backup_log` row's `restored_at` if one exists (best-effort -- a missing
    log row, e.g. for a backup taken before Step 15 existed, must not block a real
    restore)."""
    if not verify_backup(path):
        raise RuntimeError(f"Refusing to restore from unverified/corrupt backup: {path}")
    dest = _db_path()
    if dest is None:
        raise RuntimeError("DATABASE_URL is not a SQLite file URL -- restore_backup doesn't apply.")
    shutil.copy2(path, dest)
    logger.warning("Restored %s over the live DB at %s.", path, dest)

    try:
        with get_session() as session:
            log_row = session.execute(select(BackupLog).where(BackupLog.backup_path == path.name)).scalars().first()
            if log_row is not None:
                log_row.restored_at = datetime.now(timezone.utc)
    except Exception as exc:
        logger.warning("backup_log restored_at update failed (restore itself already succeeded): %s", exc)


def restore_last_verified_backup() -> Path:
    """Spec §7.13's rollback path: 'If integrity validation fails, restore the last
    verified backup.' Raises if no backup exists at all -- there is nothing safe to
    restore to, which is itself a Hard Stop condition for the caller to handle, not
    something this function can silently paper over."""
    backup = latest_backup()
    if backup is None:
        raise RuntimeError("No backup exists to restore from.")
    restore_backup(backup)
    return backup


def latest_backup() -> Path | None:
    """The most recently created backup file, or None if none exist yet."""
    backups = sorted(BACKUP_DIR.glob("finsight_*.db"), key=lambda p: p.stat().st_mtime)
    return backups[-1] if backups else None
