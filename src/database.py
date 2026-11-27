"""Database schema and connection management for Music Library Integrity Manager."""

import sqlite3
from pathlib import Path
from typing import Optional, Union

SCHEMA_VERSION = 1

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS libraries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    root_path TEXT NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS albums (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    library_id INTEGER NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
    album_rel_path TEXT NOT NULL,
    display_name TEXT,
    registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'active',
    UNIQUE(library_id, album_rel_path)
);

CREATE TABLE IF NOT EXISTS file_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    album_id INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
    rel_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    file_type TEXT DEFAULT 'audio', -- 'audio', 'companion', 'manifest'
    file_size INTEGER,
    registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(album_id, rel_path)
);

CREATE INDEX IF NOT EXISTS idx_file_records_sha256 ON file_records(sha256);
CREATE INDEX IF NOT EXISTS idx_file_records_rel_path ON file_records(rel_path);

CREATE TABLE IF NOT EXISTS import_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    library_id INTEGER NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
    source_manifest TEXT NOT NULL,
    records_imported INTEGER DEFAULT 0,
    records_skipped INTEGER DEFAULT 0,
    records_conflicted INTEGER DEFAULT 0,
    status TEXT NOT NULL,
    imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS verification_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    library_id INTEGER NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
    album_id INTEGER REFERENCES albums(id) ON DELETE SET NULL,
    scope TEXT NOT NULL, -- 'album', 'selected', 'library'
    files_checked INTEGER DEFAULT 0,
    files_matched INTEGER DEFAULT 0,
    files_changed INTEGER DEFAULT 0,
    files_missing INTEGER DEFAULT 0,
    files_unreadable INTEGER DEFAULT 0,
    status TEXT NOT NULL,
    verified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    details TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def init_db(db_path: Union[str, Path]) -> sqlite3.Connection:
    """Initializes SQLite database with foreign keys, WAL mode, and tables."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")

    with conn:
        conn.executescript(SCHEMA_SQL)
        cur = conn.execute("SELECT version FROM schema_version WHERE version = ?", (SCHEMA_VERSION,))
        if not cur.fetchone():
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))

    return conn


def backup_db(source_conn: sqlite3.Connection, target_path: Union[str, Path]) -> None:
    """Creates an online, transaction-safe backup of the SQLite database."""
    target_path = Path(target_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_conn = sqlite3.connect(str(target_path))
    with target_conn:
        source_conn.backup(target_conn)
    target_conn.close()
