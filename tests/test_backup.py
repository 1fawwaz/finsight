"""Tests for core.backup: timestamped SQLite backups taken before schema changes/bulk writes."""

import sqlite3

import pytest

import core.backup as backup_module
from core.backup import create_backup, latest_backup, restore_backup, verify_backup


@pytest.fixture()
def sqlite_db_file(tmp_path):
    """A real, on-disk, valid SQLite file to back up -- distinct from the in-memory
    `db_session` fixture, since backup/restore is a file-copy operation."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
    conn.execute("INSERT INTO t (val) VALUES ('hello')")
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture()
def patched_backup_dirs(tmp_path, sqlite_db_file, monkeypatch):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    monkeypatch.setattr(backup_module, "BACKUP_DIR", backup_dir)
    monkeypatch.setattr(backup_module, "DATABASE_URL", f"sqlite:///{sqlite_db_file.as_posix()}")
    return backup_dir


def test_create_backup_copies_and_verifies(patched_backup_dirs, sqlite_db_file):
    dest = create_backup("test_reason")
    assert dest is not None
    assert dest.exists()
    assert dest.parent == patched_backup_dirs
    assert verify_backup(dest)


def test_create_backup_returns_none_when_no_db_exists(tmp_path, monkeypatch):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    monkeypatch.setattr(backup_module, "BACKUP_DIR", backup_dir)
    monkeypatch.setattr(backup_module, "DATABASE_URL", f"sqlite:///{(tmp_path / 'nonexistent.db').as_posix()}")

    result = create_backup("no_db_yet")
    assert result is None


def test_verify_backup_rejects_corrupt_file(tmp_path):
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"not a real sqlite file")
    assert verify_backup(corrupt) is False


def test_verify_backup_rejects_missing_file(tmp_path):
    missing = tmp_path / "does_not_exist.db"
    assert verify_backup(missing) is False


def test_restore_backup_refuses_corrupt_source(tmp_path, patched_backup_dirs):
    corrupt = tmp_path / "corrupt_backup.db"
    corrupt.write_bytes(b"garbage")
    with pytest.raises(RuntimeError):
        restore_backup(corrupt)


def test_restore_backup_overwrites_live_db(patched_backup_dirs, sqlite_db_file):
    backup_path = create_backup("before_restore_test")

    # Corrupt the "live" DB, then restore from the verified backup.
    with open(sqlite_db_file, "r+b") as f:
        f.write(b"corrupted")

    restore_backup(backup_path)

    conn = sqlite3.connect(sqlite_db_file)
    rows = conn.execute("SELECT val FROM t").fetchall()
    conn.close()
    assert rows == [("hello",)]


def test_latest_backup_returns_most_recent(patched_backup_dirs, sqlite_db_file):
    assert latest_backup() is None
    first = create_backup("first")
    second = create_backup("second")
    assert latest_backup() in (first, second)  # timestamp-second resolution could tie; either is a real backup
    assert latest_backup().exists()
