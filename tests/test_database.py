"""Unit tests for the SQLite database layer (Phase 2)."""

import sqlite3
import pytest
from src.database import init_db, backup_db, SCHEMA_VERSION


def test_init_db_in_memory():
    conn = init_db(":memory:")
    assert conn is not None

    # Verify foreign keys are enabled
    cur = conn.execute("PRAGMA foreign_keys;")
    assert cur.fetchone()[0] == 1

    # Verify schema version
    cur = conn.execute("SELECT version FROM schema_version;")
    row = cur.fetchone()
    assert row is not None
    assert row["version"] == SCHEMA_VERSION


def test_foreign_key_enforcement():
    conn = init_db(":memory:")

    # Attempting to insert an album without a library should fail
    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute(
                "INSERT INTO albums (library_id, album_rel_path) VALUES (?, ?);",
                (999, "Nonexistent/Album"),
            )


def test_library_and_album_insertion():
    conn = init_db(":memory:")

    with conn:
        cur = conn.execute(
            "INSERT INTO libraries (name, root_path) VALUES (?, ?);",
            ("Main Music", "D:\\Music"),
        )
        library_id = cur.lastrowid

        cur = conn.execute(
            "INSERT INTO albums (library_id, album_rel_path, display_name) VALUES (?, ?, ?);",
            (library_id, "Artist/Album", "Album"),
        )
        album_id = cur.lastrowid

        conn.execute(
            """INSERT INTO file_records (album_id, rel_path, sha256, file_type)
               VALUES (?, ?, ?, ?);""",
            (album_id, "Artist/Album/01.flac", "a" * 64, "audio"),
        )

    # Verify retrieval
    cur = conn.execute(
        """SELECT f.rel_path, f.sha256, a.display_name, l.name
           FROM file_records f
           JOIN albums a ON f.album_id = a.id
           JOIN libraries l ON a.library_id = l.id;"""
    )
    record = cur.fetchone()
    assert record["rel_path"] == "Artist/Album/01.flac"
    assert record["sha256"] == "a" * 64
    assert record["display_name"] == "Album"
    assert record["name"] == "Main Music"


def test_unique_constraint_on_files():
    conn = init_db(":memory:")
    with conn:
        cur = conn.execute("INSERT INTO libraries (name, root_path) VALUES (?, ?);", ("L", "D:\\M"))
        lib_id = cur.lastrowid
        cur = conn.execute("INSERT INTO albums (library_id, album_rel_path) VALUES (?, ?);", (lib_id, "A"))
        alb_id = cur.lastrowid
        conn.execute(
            "INSERT INTO file_records (album_id, rel_path, sha256) VALUES (?, ?, ?);",
            (alb_id, "01.flac", "b" * 64),
        )

    # Duplicate relative path in same album should raise IntegrityError
    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute(
                "INSERT INTO file_records (album_id, rel_path, sha256) VALUES (?, ?, ?);",
                (alb_id, "01.flac", "c" * 64),
            )


def test_backup_db(tmp_path):
    source_db = tmp_path / "source.db"
    backup_file = tmp_path / "backup.db"

    conn = init_db(source_db)
    with conn:
        conn.execute("INSERT INTO libraries (name, root_path) VALUES (?, ?);", ("Test", "D:\\Test"))

    backup_db(conn, backup_file)
    conn.close()

    assert backup_file.exists()

    # Verify backup contents
    backup_conn = sqlite3.connect(str(backup_file))
    backup_conn.row_factory = sqlite3.Row
    cur = backup_conn.execute("SELECT name, root_path FROM libraries;")
    row = cur.fetchone()
    assert row["name"] == "Test"
    assert row["root_path"] == "D:\\Test"
    backup_conn.close()
