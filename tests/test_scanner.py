"""Unit tests for the physical library scanner (Phase 7)."""

import pytest
from pathlib import Path
from src.database import init_db
from src.scanner import scan_library


def test_scan_library_empty(tmp_path):
    conn = init_db(":memory:")
    empty_lib = tmp_path / "empty_music"
    empty_lib.mkdir()

    with conn:
        cur = conn.execute("INSERT INTO libraries (name, root_path) VALUES (?, ?);", ("Empty", str(empty_lib)))
        lib_id = cur.lastrowid

    report = scan_library(conn, lib_id)
    assert report.total_physical_files == 0
    assert report.registered_files_count == 0
    assert len(report.unregistered_files) == 0
    assert len(report.missing_registered_files) == 0
    assert len(report.potential_new_albums) == 0
    assert report.was_cancelled is False


def test_scan_library_discrepancies(tmp_path):
    conn = init_db(":memory:")
    lib_dir = tmp_path / "music"
    lib_dir.mkdir()

    # 1. Registered album that is present on disk
    reg_album_dir = lib_dir / "Artist" / "RegisteredAlbum"
    reg_album_dir.mkdir(parents=True)
    f_reg = reg_album_dir / "01.flac"
    f_reg.write_text("audio1")

    # 2. Registered file that is MISSING on disk
    with conn:
        cur = conn.execute("INSERT INTO libraries (name, root_path) VALUES (?, ?);", ("Lib", str(lib_dir)))
        lib_id = cur.lastrowid
        cur = conn.execute("INSERT INTO albums (library_id, album_rel_path, display_name) VALUES (?, ?, ?);",
                           (lib_id, "Artist\\RegisteredAlbum", "RegisteredAlbum"))
        alb_id = cur.lastrowid
        conn.execute("INSERT INTO file_records (album_id, rel_path, sha256) VALUES (?, ?, ?);",
                     (alb_id, "01.flac", "a" * 64))
        # Missing file registered in DB:
        conn.execute("INSERT INTO file_records (album_id, rel_path, sha256) VALUES (?, ?, ?);",
                     (alb_id, "missing.flac", "b" * 64))

    # 3. New potential album on disk not in DB
    new_album_dir = lib_dir / "NewArtist" / "NewAlbum"
    new_album_dir.mkdir(parents=True)
    (new_album_dir / "01.flac").write_text("new audio")
    (new_album_dir / "02.flac").write_text("new audio 2")
    (new_album_dir / "cover.jpg").write_text("artwork")
    (new_album_dir / "NewAlbum.sha256").write_text(f"{'c'*64} *01.flac\n")

    # 4. Loose unregistered file
    (lib_dir / "loose_unregistered.txt").write_text("notes")

    report = scan_library(conn, lib_id)

    assert report.registered_files_count == 2
    # Missing registered file should be detected
    assert any("missing.flac" in f for f in report.missing_registered_files)

    # Unregistered files: loose_unregistered, 01.flac in new album, 02.flac, cover.jpg, manifest
    assert len(report.unregistered_files) == 5
    assert any("loose_unregistered.txt" in f for f in report.unregistered_files)

    # Potential new album detected
    assert len(report.potential_new_albums) == 1
    pot = report.potential_new_albums[0]
    assert "NewArtist\\NewAlbum" in pot.album_rel_path
    assert pot.audio_files_count == 2
    assert pot.companion_files_count == 2
    assert pot.manifest_found == "NewAlbum.sha256"


def test_scan_library_cancellation(tmp_path):
    conn = init_db(":memory:")
    lib_dir = tmp_path / "music"
    lib_dir.mkdir()

    for i in range(10):
        (lib_dir / f"track{i}.flac").write_text(f"audio {i}")

    with conn:
        cur = conn.execute("INSERT INTO libraries (name, root_path) VALUES (?, ?);", ("Lib", str(lib_dir)))
        lib_id = cur.lastrowid

    def cancel_after_3(count, path):
        return count < 3

    report = scan_library(conn, lib_id, progress_callback=cancel_after_3)
    assert report.was_cancelled is True
    assert report.total_physical_files == 3
