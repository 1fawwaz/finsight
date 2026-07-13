"""Timestamped SQLite backups, taken before any schema change or bulk write.

Minimal primitive pulled forward from Phase 1 Step 15 (Backup and rollback support)
because the operating spec's own Prime Directive -- "never write to the database without
a fresh backup in place first" -- has to be satisfiable before Step 1 (Symbol Registry)
can touch the real database at all. `backup_log` persistence and restore-on-failure wiring
are the rest of Step 15, built on top of this.
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from core.config import DATA_DIR, DATABASE_URL, get_logger

logger = get_logger(__name__)

BACKUP_DIR = DATA_DIR / "backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

_SQLITE_PREFIX = "sqlite:///"


def _db_path() -> Path | None:
    """The on-disk path of the current SQLite DB, or None if DATABASE_URL isn't SQLite
    (e.g. a future PostgreSQL DATABASE_URL) -- backups are a SQLite-file-copy operation
    and don't apply to a server-based engine the same way."""
    if not DATABASE_URL.startswith(_SQLITE_PREFIX):
        return None
    return Path(DATABASE_URL[len(_SQLITE_PREFIX):])


def create_backup(reason: str) -> Path | None:
    """Copy the live DB file to a timestamped backup, verified readable before returning.

    Returns None (logged, not raised) if there is no DB file yet to back up -- a fresh
    install with no `finsight.db` created yet has nothing to protect. Raises if a DB file
    exists but the resulting backup fails to verify, since silently proceeding past a
    corrupt/incomplete backup would violate the "never write without a fresh backup in
    place" rule in spirit even though a file was technically copied.
    """
    src = _db_path()
    if src is None or not src.exists():
        logger.info("No existing SQLite DB at %s -- nothing to back up before this write.", src)
        return None

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_reason = "".join(c if c.isalnum() or c in "-_" else "_" for c in reason)
    dest = BACKUP_DIR / f"finsight_{safe_reason}_{timestamp}.db"
    shutil.copy2(src, dest)

    if not verify_backup(dest):
        dest.unlink(missing_ok=True)
        raise RuntimeError(f"Backup verification failed for {dest} -- backup removed, not left in a false-good state.")

    logger.info("Backup created and verified: %s (reason=%s)", dest, reason)
    return dest


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
    never restores from a backup that doesn't itself pass integrity_check."""
    if not verify_backup(path):
        raise RuntimeError(f"Refusing to restore from unverified/corrupt backup: {path}")
    dest = _db_path()
    if dest is None:
        raise RuntimeError("DATABASE_URL is not a SQLite file URL -- restore_backup doesn't apply.")
    shutil.copy2(path, dest)
    logger.warning("Restored %s over the live DB at %s.", path, dest)


def latest_backup() -> Path | None:
    """The most recently created backup file, or None if none exist yet."""
    backups = sorted(BACKUP_DIR.glob("finsight_*.db"), key=lambda p: p.stat().st_mtime)
    return backups[-1] if backups else None
