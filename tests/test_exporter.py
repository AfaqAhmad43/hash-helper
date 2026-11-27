"""Unit tests for master manifest generation (Phase 5)."""

import pytest
from pathlib import Path
from src.database import init_db
from src.exporter import generate_master_manifest
from src.importer import parse_manifest_file
from src.registration import preview_album_registration, register_album


def test_generate_master_manifest_empty(tmp_path):
    conn = init_db(":memory:")
    with conn:
        cur = conn.execute("INSERT INTO libraries (name, root_path) VALUES (?, ?);", ("EmptyLib", str(tmp_path)))
        lib_id = cur.lastrowid

    out_file = tmp_path / "master.sha256"
    report = generate_master_manifest(conn, lib_id, out_file)

    assert report.success is True
    assert report.records_exported == 0
    assert out_file.exists()
    assert out_file.read_text(encoding="utf-8") == ""


def test_generate_master_manifest_from_registered_albums(tmp_path):
    conn = init_db(":memory:")
    sample_dir = Path("sample_data/manifests").resolve()
    with conn:
        cur = conn.execute("INSERT INTO libraries (name, root_path) VALUES (?, ?);", ("TestLib", str(sample_dir)))
        lib_id = cur.lastrowid

    # Register sample albums
    alb1 = sample_dir / "[2025] After EP 2 [CD - None]"
    alb2 = sample_dir / "[2026] Fell Asleep in the Sun [Digital - Deezer]"

    res1 = register_album(conn, preview_album_registration(conn, lib_id, alb1))
    res2 = register_album(conn, preview_album_registration(conn, lib_id, alb2))
    assert res1.success and res2.success

    # Export master manifest
    out_file = tmp_path / "exported_master.sha256"
    report = generate_master_manifest(conn, lib_id, out_file, library_prefix="Organized")

    assert report.success is True
    expected_count = res1.imported_count + res2.imported_count
    assert report.records_exported == expected_count

    # Verify exported file format
    content_bytes = out_file.read_bytes()
    assert b"\r\n" in content_bytes  # CRLF line endings
    assert not content_bytes.startswith(b"\xef\xbb\xbf")  # No UTF-8 BOM

    # Verify exported manifest parses back cleanly with importer parser
    records, malformed = parse_manifest_file(out_file)
    assert len(malformed) == 0
    assert len(records) == expected_count
    for r in records:
        assert r.rel_path.startswith("Organized\\")
        assert len(r.sha256) == 64


def test_manifest_export_no_silent_overwrite(tmp_path):
    conn = init_db(":memory:")
    with conn:
        cur = conn.execute("INSERT INTO libraries (name, root_path) VALUES (?, ?);", ("Lib", str(tmp_path)))
        lib_id = cur.lastrowid

    out_file = tmp_path / "existing.sha256"
    out_file.write_text("existing content\n", encoding="utf-8")

    # Attempting to export without overwrite should fail safely
    report = generate_master_manifest(conn, lib_id, out_file, overwrite=False)
    assert report.success is False
    assert "already exists" in report.message
    assert out_file.read_text(encoding="utf-8") == "existing content\n"

    # Export with overwrite=True succeeds
    report_overwrite = generate_master_manifest(conn, lib_id, out_file, overwrite=True)
    assert report_overwrite.success is True
