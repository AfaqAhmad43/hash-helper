"""Unit tests for database backup and recovery operations (Phase 9)."""

import sqlite3
import pytest
from pathlib import Path
from src.backup import (
    create_backup,
    list_backups,
    restore_backup,
    verify_database_integrity,
)
from src.database import init_db


def test_verify_database_integrity_valid_and_corrupt(tmp_path):
    # Valid DB
    valid_db = tmp_path / "valid.db"
    conn = init_db(valid_db)
    conn.close()
    assert verify_database_integrity(valid_db) is True

    # Corrupt / random binary file
    corrupt_db = tmp_path / "corrupt.db"
    corrupt_db.write_bytes(b"garbage binary data not a valid sqlite database header!!")
    assert verify_database_integrity(corrupt_db) is False


def test_create_backup_and_list(tmp_path):
    source_db = tmp_path / "source.db"
    backup_dir = tmp_path / "backups"

    conn = init_db(source_db)
    with conn:
        conn.execute("INSERT INTO libraries (name, root_path) VALUES (?, ?);", ("BackupLib", "D:\\Music"))
    conn.close()

    backup_file = create_backup(source_db, backup_dir)
    assert backup_file.exists()
    assert verify_database_integrity(backup_file) is True

    backups = list_backups(backup_dir)
    assert len(backups) == 1
    assert backups[0].file_name == backup_file.name
    assert backups[0].is_valid is True
    assert backups[0].size_bytes > 0


def test_restore_backup_with_safety_copy(tmp_path):
    original_db = tmp_path / "main.db"
    backup_dir = tmp_path / "backups"

    # 1. Create original DB with State 1
    conn = init_db(original_db)
    with conn:
        conn.execute("INSERT INTO libraries (name, root_path) VALUES (?, ?);", ("State1", "D:\\Music1"))
    conn.close()

    # 2. Take backup of State 1
    backup_file = create_backup(original_db, backup_dir)

    # 3. Alter original DB to State 2
    conn2 = sqlite3.connect(str(original_db))
    with conn2:
        conn2.execute("UPDATE libraries SET name = 'State2' WHERE id = 1;")
    conn2.close()

    # Verify State 2 is active
    conn_check = sqlite3.connect(str(original_db))
    assert conn_check.execute("SELECT name FROM libraries WHERE id = 1;").fetchone()[0] == "State2"
    conn_check.close()

    # 4. Restore State 1 from backup
    safety_copy = restore_backup(backup_file, original_db, create_safety_copy=True)
    assert safety_copy is not None
    assert safety_copy.exists()

    # Verify original DB was restored to State 1
    conn_restored = sqlite3.connect(str(original_db))
    assert conn_restored.execute("SELECT name FROM libraries WHERE id = 1;").fetchone()[0] == "State1"
    conn_restored.close()

    # Verify safety copy retained State 2
    conn_safety = sqlite3.connect(str(safety_copy))
    assert conn_safety.execute("SELECT name FROM libraries WHERE id = 1;").fetchone()[0] == "State2"
    conn_safety.close()
