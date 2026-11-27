"""Backup and recovery management module for Music Library Integrity Manager.

Provides online, transaction-safe database backups, restoration with
WAL-safe pre-restore safety copies, and SQLite integrity validation.
"""

import datetime
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Union

from src.database import backup_db


@dataclass
class BackupInfo:
    file_path: Path
    file_name: str
    size_bytes: int
    created_at: str
    is_valid: bool


def verify_database_integrity(db_path: Union[str, Path]) -> bool:
    """Runs PRAGMA integrity_check on an SQLite database.

    Returns True if database is healthy ('ok'), False otherwise.
    """
    path = Path(db_path)
    if not path.exists() or path.stat().st_size == 0:
        return False

    try:
        conn = sqlite3.connect(str(path))
        cur = conn.cursor()
        cur.execute("PRAGMA integrity_check;")
        res = cur.fetchone()
        conn.close()
        return res is not None and res[0] == "ok"
    except Exception:
        return False


def create_backup(
    db_path: Union[str, Path],
    backup_dir: Union[str, Path],
    prefix: str = "integrity_backup",
) -> Path:
    """Creates a timestamped, online, transaction-safe backup of the SQLite database.

    Forces a checkpoint on WAL mode to ensure all uncommitted/uncheckpointed
    transactions are cleanly flushed before taking the backup.
    Verifies the integrity of the created backup before returning.
    """
    source_db = Path(db_path).resolve()
    if not source_db.exists():
        raise FileNotFoundError(f"Database file not found: {source_db}")

    target_dir = Path(backup_dir).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = target_dir / f"{prefix}_{timestamp}.db"

    source_conn = sqlite3.connect(str(source_db))
    try:
        # Checkpoint WAL pages into database file
        source_conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        backup_db(source_conn, backup_file)
    finally:
        source_conn.close()

    # Validate integrity of the created backup
    if not verify_database_integrity(backup_file):
        if backup_file.exists():
            backup_file.unlink()
        raise RuntimeError("Created backup failed PRAGMA integrity_check.")

    return backup_file


def list_backups(backup_dir: Union[str, Path]) -> List[BackupInfo]:
    """Lists and inspects all database backups in the target directory."""
    target_dir = Path(backup_dir).resolve()
    if not target_dir.exists():
        return []

    backups: List[BackupInfo] = []
    for f in sorted(target_dir.glob("*.db"), key=os.path.getmtime, reverse=True):
        stat = f.stat()
        created_time = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        valid = verify_database_integrity(f)
        backups.append(
            BackupInfo(
                file_path=f,
                file_name=f.name,
                size_bytes=stat.st_size,
                created_at=created_time,
                is_valid=valid,
            )
        )

    return backups


def restore_backup(
    backup_path: Union[str, Path],
    target_db_path: Union[str, Path],
    create_safety_copy: bool = True,
) -> Optional[Path]:
    """Restores database from a verified backup file.

    Safety:
    1. Validates integrity of backup before proceeding.
    2. Creates a transaction-safe pre-restore snapshot using sqlite3.backup()
       instead of raw file copy to capture WAL state.
    3. Restores cleanly using SQLite backup API.
    """
    backup_file = Path(backup_path).resolve()
    if not backup_file.exists():
        raise FileNotFoundError(f"Backup file not found: {backup_file}")

    if not verify_database_integrity(backup_file):
        raise ValueError(f"Backup file failed integrity check and cannot be restored: {backup_file}")

    target_db = Path(target_db_path).resolve()
    safety_copy_path: Optional[Path] = None

    if target_db.exists() and create_safety_copy:
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        safety_copy_path = target_db.parent / f"{target_db.stem}_pre_restore_safety_{timestamp}.db"

        # Capture WAL-safe snapshot via backup API
        target_conn = sqlite3.connect(str(target_db))
        try:
            target_conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            backup_db(target_conn, safety_copy_path)
        finally:
            target_conn.close()

    # Use SQLite backup API to safely restore backup into target
    target_db.parent.mkdir(parents=True, exist_ok=True)
    src_conn = sqlite3.connect(str(backup_file))
    dst_conn = sqlite3.connect(str(target_db))
    try:
        with dst_conn:
            src_conn.backup(dst_conn)
        dst_conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    finally:
        src_conn.close()
        dst_conn.close()

    return safety_copy_path
