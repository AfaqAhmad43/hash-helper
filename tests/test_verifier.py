"""Unit and integration tests for the repaired verification engine (Phase 1 / Task 3)."""

import hashlib
import sqlite3
import pytest
from pathlib import Path
from src.database import init_db
from src.verifier import (
    calculate_file_sha256,
    validate_scope_and_albums,
    verify_files,
)


def test_calculate_file_sha256_with_cancellation(tmp_path):
    test_file = tmp_path / "test.bin"
    # 256 KB file (4 chunks)
    content = b"X" * (256 * 1024)
    test_file.write_bytes(content)

    expected = hashlib.sha256(content).hexdigest().lower()
    actual = calculate_file_sha256(test_file)
    assert actual == expected

    # Test cancellation triggers mid-read
    call_count = 0

    def cancel_on_second_chunk():
        nonlocal call_count
        call_count += 1
        return call_count >= 2

    cancelled_hash = calculate_file_sha256(test_file, cancel_check=cancel_on_second_chunk)
    assert cancelled_hash is None


def test_scope_validation_rules(tmp_path):
    conn = init_db(":memory:")
    with conn:
        cur = conn.execute("INSERT INTO libraries (name, root_path) VALUES (?, ?);", ("Lib1", str(tmp_path / "lib1")))
        lib1_id = cur.lastrowid
        cur = conn.execute("INSERT INTO libraries (name, root_path) VALUES (?, ?);", ("Lib2", str(tmp_path / "lib2")))
        lib2_id = cur.lastrowid

        cur = conn.execute("INSERT INTO albums (library_id, album_rel_path) VALUES (?, ?);", (lib1_id, "Album1"))
        alb1_id = cur.lastrowid
        cur = conn.execute("INSERT INTO albums (library_id, album_rel_path) VALUES (?, ?);", (lib2_id, "Album2"))
        alb2_id = cur.lastrowid

    # 1. Invalid scope name
    with pytest.raises(ValueError, match="Invalid verification scope"):
        validate_scope_and_albums(conn, lib1_id, "everything", None)

    # 2. Scope album without ID
    with pytest.raises(ValueError, match="Scope 'album' requires exactly one valid album ID"):
        validate_scope_and_albums(conn, lib1_id, "album", None)

    # 3. Scope album with empty list
    with pytest.raises(ValueError, match="Scope 'album' requires exactly one valid album ID"):
        validate_scope_and_albums(conn, lib1_id, "album", [])

    # 4. Scope album with multiple IDs
    with pytest.raises(ValueError, match="Scope 'album' requires exactly one valid album ID"):
        validate_scope_and_albums(conn, lib1_id, "album", [alb1_id, 999])

    # 5. Scope selected with empty list
    with pytest.raises(ValueError, match="Scope 'selected' requires a non-empty list"):
        validate_scope_and_albums(conn, lib1_id, "selected", [])

    # 6. Scope selected with foreign library album ID
    with pytest.raises(ValueError, match="do not belong to library"):
        validate_scope_and_albums(conn, lib1_id, "selected", [alb2_id])

    # 7. Valid calls
    assert validate_scope_and_albums(conn, lib1_id, "library", None) == []
    assert validate_scope_and_albums(conn, lib1_id, "album", [alb1_id]) == [alb1_id]
    assert validate_scope_and_albums(conn, lib1_id, "selected", [alb1_id]) == [alb1_id]


def test_path_containment_and_traversal_rejection(tmp_path):
    conn = init_db(":memory:")
    lib_dir = tmp_path / "music_root"
    lib_dir.mkdir()

    # Secret file outside library root
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("classified")

    with conn:
        cur = conn.execute("INSERT INTO libraries (name, root_path) VALUES (?, ?);", ("SafeLib", str(lib_dir)))
        lib_id = cur.lastrowid
        cur = conn.execute("INSERT INTO albums (library_id, album_rel_path) VALUES (?, ?);", (lib_id, "Album1"))
        alb_id = cur.lastrowid

        # Malicious traversal path attempting to escape library root
        conn.execute(
            "INSERT INTO file_records (album_id, rel_path, sha256) VALUES (?, ?, ?);",
            (alb_id, "../../secret.txt", "0" * 64),
        )

    summary = verify_files(conn, lib_id, scope="library")
    assert summary.files_invalid_path == 1
    assert summary.files_checked == 1
    assert summary.results[0].status == "invalid_path"
    assert "outside library root" in summary.results[0].error_message


def test_verify_files_all_statuses(tmp_path):
    conn = init_db(":memory:")
    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()

    with conn:
        cur = conn.execute("INSERT INTO libraries (name, root_path) VALUES (?, ?);", ("TestLib", str(lib_dir)))
        lib_id = cur.lastrowid
        cur = conn.execute("INSERT INTO albums (library_id, album_rel_path, display_name) VALUES (?, ?, ?);",
                           (lib_id, "Album1", "Album1"))
        album_id = cur.lastrowid

        # 1. Verified
        f_ver = lib_dir / "Album1" / "01.flac"
        f_ver.parent.mkdir(parents=True, exist_ok=True)
        f_ver.write_bytes(b"Good track")
        hash_ver = calculate_file_sha256(f_ver)
        conn.execute("INSERT INTO file_records (album_id, rel_path, sha256) VALUES (?, ?, ?);",
                     (album_id, "01.flac", hash_ver))

        # 2. Changed
        f_changed = lib_dir / "Album1" / "02.flac"
        f_changed.write_bytes(b"Altered track")
        conn.execute("INSERT INTO file_records (album_id, rel_path, sha256) VALUES (?, ?, ?);",
                     (album_id, "02.flac", "0" * 64))

        # 3. Missing
        conn.execute("INSERT INTO file_records (album_id, rel_path, sha256) VALUES (?, ?, ?);",
                     (album_id, "03.flac", "1" * 64))

    summary = verify_files(conn, lib_id, scope="album", album_ids=[album_id])
    assert summary.files_checked == 3
    assert summary.files_matched == 1
    assert summary.files_changed == 1
    assert summary.files_missing == 1
    assert summary.files_invalid_path == 0
    assert summary.was_cancelled is False


def test_verify_files_cancellation_during_hashing(tmp_path):
    conn = init_db(":memory:")
    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()

    with conn:
        cur = conn.execute("INSERT INTO libraries (name, root_path) VALUES (?, ?);", ("TestLib", str(lib_dir)))
        lib_id = cur.lastrowid
        cur = conn.execute("INSERT INTO albums (library_id, album_rel_path) VALUES (?, ?);", (lib_id, "Album1"))
        album_id = cur.lastrowid

        for i in range(4):
            f = lib_dir / "Album1" / f"track_{i}.flac"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"AudioData" * 10000)
            conn.execute("INSERT INTO file_records (album_id, rel_path, sha256) VALUES (?, ?, ?);",
                         (album_id, f"track_{i}.flac", calculate_file_sha256(f)))

    # Cancel after checking 2 files
    summary = verify_files(
        conn,
        lib_id,
        scope="library",
        progress_callback=lambda curr, total, p: curr < 2,
    )
    assert summary.was_cancelled is True
    assert summary.files_checked == 2
