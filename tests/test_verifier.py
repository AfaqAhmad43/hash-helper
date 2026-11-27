"""Unit tests for the verification engine (Phase 6)."""

import hashlib
import pytest
from pathlib import Path
from src.database import init_db
from src.verifier import calculate_file_sha256, verify_files


def test_calculate_file_sha256(tmp_path):
    test_file = tmp_path / "test.txt"
    content = b"Hello, Music Integrity Manager!"
    test_file.write_bytes(content)

    expected = hashlib.sha256(content).hexdigest().lower()
    actual = calculate_file_sha256(test_file)
    assert actual == expected


def test_verify_files_all_statuses(tmp_path):
    conn = init_db(":memory:")
    library_dir = tmp_path / "lib"
    library_dir.mkdir()

    with conn:
        cur = conn.execute("INSERT INTO libraries (name, root_path) VALUES (?, ?);", ("TestLib", str(library_dir)))
        lib_id = cur.lastrowid
        cur = conn.execute("INSERT INTO albums (library_id, album_rel_path, display_name) VALUES (?, ?, ?);", (lib_id, "Album1", "Album1"))
        album_id = cur.lastrowid

        # 1. Verified file (content matches hash)
        f_verified = library_dir / "Album1" / "verified.flac"
        f_verified.parent.mkdir(parents=True, exist_ok=True)
        f_verified.write_bytes(b"Track 1 Audio Data")
        hash_verified = calculate_file_sha256(f_verified)
        conn.execute("INSERT INTO file_records (album_id, rel_path, sha256) VALUES (?, ?, ?);", (album_id, "verified.flac", hash_verified))

        # 2. Changed file (disk content differs from registered baseline)
        f_changed = library_dir / "Album1" / "changed.flac"
        f_changed.write_bytes(b"Modified Track 2 Audio Data")
        conn.execute("INSERT INTO file_records (album_id, rel_path, sha256) VALUES (?, ?, ?);", (album_id, "changed.flac", "0" * 64))

        # 3. Missing file (registered but does not exist on disk)
        conn.execute("INSERT INTO file_records (album_id, rel_path, sha256) VALUES (?, ?, ?);", (album_id, "missing.flac", "1" * 64))

    summary = verify_files(conn, lib_id, scope="album", album_ids=[album_id])

    assert summary.files_checked == 3
    assert summary.files_matched == 1
    assert summary.files_changed == 1
    assert summary.files_missing == 1
    assert summary.files_unreadable == 0
    assert summary.was_cancelled is False

    # Check individual result details
    status_map = {r.rel_path: r.status for r in summary.results}
    assert status_map["verified.flac"] == "verified"
    assert status_map["changed.flac"] == "changed"
    assert status_map["missing.flac"] == "missing"

    # Verify history entry in database
    cur = conn.execute("SELECT * FROM verification_history WHERE library_id = ?;", (lib_id,))
    hist = cur.fetchone()
    assert hist["status"] == "discrepancies_detected"
    assert hist["files_matched"] == 1
    assert hist["files_changed"] == 1
    assert hist["files_missing"] == 1


def test_verify_files_cancellation(tmp_path):
    conn = init_db(":memory:")
    library_dir = tmp_path / "lib"
    library_dir.mkdir()

    with conn:
        cur = conn.execute("INSERT INTO libraries (name, root_path) VALUES (?, ?);", ("TestLib", str(library_dir)))
        lib_id = cur.lastrowid
        cur = conn.execute("INSERT INTO albums (library_id, album_rel_path, display_name) VALUES (?, ?, ?);", (lib_id, "Album1", "Album1"))
        album_id = cur.lastrowid

        for i in range(5):
            f = library_dir / "Album1" / f"track{i}.flac"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(f"audio {i}".encode())
            conn.execute("INSERT INTO file_records (album_id, rel_path, sha256) VALUES (?, ?, ?);", (album_id, f"track{i}.flac", calculate_file_sha256(f)))

    # Progress callback that cancels after checking 2 files
    def cancel_after_2(current, total, path):
        return current < 2

    summary = verify_files(conn, lib_id, scope="library", progress_callback=cancel_after_2)
    assert summary.was_cancelled is True
    assert summary.files_checked == 2
