"""Unit tests for the RapidCRC manifest importer (Phase 3)."""

import pytest
from pathlib import Path
from src.database import init_db
from src.importer import (
    parse_manifest_file,
    classify_file_type,
    preview_import,
    commit_import,
)


def test_classify_file_type():
    assert classify_file_type("track.flac") == "audio"
    assert classify_file_type("TRACK.FLAC") == "audio"
    assert classify_file_type("cover.jpg") == "companion"
    assert classify_file_type("album.cue") == "companion"
    assert classify_file_type("album.sha256") == "manifest"
    assert classify_file_type("random.xyz") == "other"


def test_parse_sample_album_manifest():
    manifest_path = Path("sample_data/manifests/[2025] After EP 2 [CD - None]/[2025] After EP 2 [CD - None].sha256")
    if not manifest_path.exists():
        pytest.skip("Sample manifest not found")

    records, malformed = parse_manifest_file(manifest_path)
    assert len(malformed) == 0
    assert len(records) > 0

    # Ensure all parsed records have 64-char lowercase hex
    for r in records:
        assert len(r.sha256) == 64
        assert r.sha256 == r.sha256.lower()
        assert r.rel_path != ""


def test_preview_and_commit_import():
    conn = init_db(":memory:")
    with conn:
        cur = conn.execute("INSERT INTO libraries (name, root_path) VALUES (?, ?);", ("TestLib", "D:\\Music"))
        library_id = cur.lastrowid

    manifest_path = Path("sample_data/manifests/[2025] After EP 2 [CD - None]/[2025] After EP 2 [CD - None].sha256")
    if not manifest_path.exists():
        pytest.skip("Sample manifest not found")

    album_rel = "[2025] After EP 2 [CD - None]"
    preview = preview_import(conn, library_id, manifest_path, album_rel_path=album_rel)

    assert preview.total_parsed == len(preview.new_records)
    assert len(preview.duplicate_records) == 0
    assert len(preview.conflict_records) == 0

    # Commit the preview
    result = commit_import(conn, library_id, preview)
    assert result["imported"] == preview.total_parsed

    # Preview again: all records should now be reported as duplicates
    preview2 = preview_import(conn, library_id, manifest_path, album_rel_path=album_rel)
    assert len(preview2.new_records) == 0
    assert len(preview2.duplicate_records) == preview.total_parsed
    assert len(preview2.conflict_records) == 0


def test_conflict_detection_and_safety():
    conn = init_db(":memory:")
    with conn:
        cur = conn.execute("INSERT INTO libraries (name, root_path) VALUES (?, ?);", ("TestLib", "D:\\Music"))
        library_id = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO albums (library_id, album_rel_path, display_name) VALUES (?, ?, ?);",
            (library_id, "Album1", "Album1"),
        )
        album_id = cur.lastrowid
        # Insert initial baseline record
        conn.execute(
            "INSERT INTO file_records (album_id, rel_path, sha256) VALUES (?, ?, ?);",
            (album_id, "01.flac", "0" * 64),
        )

    # Create dummy manifest with conflicting checksum
    tmp_manifest = Path("tests/dummy_conflict.sha256")
    tmp_manifest.write_text(f"{'1' * 64} *01.flac\n", encoding="utf-8")

    try:
        preview = preview_import(conn, library_id, tmp_manifest, album_rel_path="Album1")
        assert len(preview.conflict_records) == 1
        assert preview.conflict_records[0].existing_sha256 == "0" * 64

        # Committing without explicit override must raise ValueError
        with pytest.raises(ValueError, match="conflicts detected"):
            commit_import(conn, library_id, preview, ignore_conflicts=False)

        # Verify database record was NOT altered
        cur = conn.execute("SELECT sha256 FROM file_records WHERE album_id = ? AND rel_path = ?", (album_id, "01.flac"))
        assert cur.fetchone()["sha256"] == "0" * 64
    finally:
        if tmp_manifest.exists():
            tmp_manifest.unlink()


def test_commit_import_rejects_stale_preview(tmp_path):
    conn = init_db(":memory:")
    with conn:
        cur = conn.execute("INSERT INTO libraries (name, root_path) VALUES (?, ?);", ("Lib", str(tmp_path)))
        lib_id = cur.lastrowid

    manifest_file = tmp_path / "album.sha256"
    manifest_file.write_text(f"{'a'*64} *01.flac\n", encoding="utf-8")

    # Generate preview
    preview = preview_import(conn, lib_id, manifest_file, album_rel_path="Album1")
    assert preview.total_parsed == 1

    # Simulate manifest alteration after preview was generated
    manifest_file.write_text(f"{'b'*64} *01.flac\n{'c'*64} *02.flac\n", encoding="utf-8")

    # Commit must reject stale preview
    with pytest.raises(ValueError, match="Source manifest was modified on disk"):
        commit_import(conn, lib_id, preview)

