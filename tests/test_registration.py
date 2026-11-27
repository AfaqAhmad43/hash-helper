"""Unit tests for incremental album registration (Phase 4)."""

import pytest
from pathlib import Path
from src.database import init_db
from src.registration import (
    find_album_manifest,
    preview_album_registration,
    register_album,
)


def test_find_album_manifest():
    album_dir = Path("sample_data/manifests/[2025] After EP 2 [CD - None]")
    if not album_dir.exists():
        pytest.skip("Sample album directory not found")

    manifest = find_album_manifest(album_dir)
    assert manifest is not None
    assert manifest.name == "[2025] After EP 2 [CD - None].sha256"


def test_preview_album_registration():
    conn = init_db(":memory:")
    # Initialize library pointing to sample_data/manifests
    lib_path = Path("sample_data/manifests").resolve()
    with conn:
        cur = conn.execute("INSERT INTO libraries (name, root_path) VALUES (?, ?);", ("TestLib", str(lib_path)))
        lib_id = cur.lastrowid

    album_dir = lib_path / "[2025] After EP 2 [CD - None]"
    preview = preview_album_registration(conn, lib_id, album_dir)

    assert preview.error_message is None
    assert preview.is_already_registered is False
    assert preview.manifest_path is not None
    assert preview.preview is not None
    assert preview.preview.total_parsed > 0
    assert len(preview.preview.new_records) == preview.preview.total_parsed


def test_register_album_incremental():
    conn = init_db(":memory:")
    lib_path = Path("sample_data/manifests").resolve()
    with conn:
        cur = conn.execute("INSERT INTO libraries (name, root_path) VALUES (?, ?);", ("TestLib", str(lib_path)))
        lib_id = cur.lastrowid

    album1_dir = lib_path / "[2025] After EP 2 [CD - None]"
    album2_dir = lib_path / "[2026] Fell Asleep in the Sun [Digital - Deezer]"

    # 1. Register first album
    preview1 = preview_album_registration(conn, lib_id, album1_dir)
    res1 = register_album(conn, preview1)
    assert res1.success is True
    assert res1.imported_count > 0

    # 2. Register second album incrementally
    preview2 = preview_album_registration(conn, lib_id, album2_dir)
    res2 = register_album(conn, preview2)
    assert res2.success is True
    assert res2.imported_count > 0

    # Verify both albums exist independently in the database
    cur = conn.execute("SELECT COUNT(*) FROM albums WHERE library_id = ?", (lib_id,))
    assert cur.fetchone()[0] == 2

    # Verify registering album 1 again detects it as duplicates and does not duplicate records
    preview1_again = preview_album_registration(conn, lib_id, album1_dir)
    assert preview1_again.is_already_registered is True
    assert len(preview1_again.preview.new_records) == 0
    assert len(preview1_again.preview.duplicate_records) == res1.imported_count

    res1_again = register_album(conn, preview1_again)
    assert res1_again.success is True
    assert res1_again.imported_count == 0
    assert res1_again.skipped_count == res1.imported_count


def test_register_album_missing_manifest(tmp_path):
    conn = init_db(":memory:")
    with conn:
        cur = conn.execute("INSERT INTO libraries (name, root_path) VALUES (?, ?);", ("Lib", str(tmp_path)))
        lib_id = cur.lastrowid

    empty_album = tmp_path / "EmptyAlbum"
    empty_album.mkdir()

    preview = preview_album_registration(conn, lib_id, empty_album)
    assert preview.error_message is not None
    assert "No .sha256 checksum manifest found" in preview.error_message

    res = register_album(conn, preview)
    assert res.success is False
    assert "No .sha256 checksum manifest found" in res.message
