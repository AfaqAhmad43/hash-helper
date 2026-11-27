"""Incremental Album Registration module for Music Library Integrity Manager.

Orchestrates adding individual albums to the library baseline without
re-reading or re-hashing previously registered music.
"""

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Union

from src.importer import (
    ImportPreview,
    commit_import,
    parse_manifest_file,
    preview_import,
)


@dataclass
class AlbumRegistrationPreview:
    library_id: int
    album_dir: Path
    album_rel_path: str
    manifest_path: Optional[Path]
    preview: Optional[ImportPreview]
    is_already_registered: bool
    existing_album_id: Optional[int] = None
    error_message: Optional[str] = None


@dataclass
class RegistrationResult:
    success: bool
    album_id: Optional[int]
    album_rel_path: str
    imported_count: int
    skipped_count: int
    conflict_count: int
    message: str


def find_album_manifest(album_dir: Union[str, Path]) -> Optional[Path]:
    """Finds a RapidCRC manifest (.sha256) inside an album directory.

    Prefers manifest matching the directory name, otherwise the first .sha256 file found.
    """
    album_path = Path(album_dir)
    if not album_path.is_dir():
        return None

    manifests = list(album_path.glob("*.sha256"))
    if not manifests:
        return None

    # Try to match album folder name
    target_name = f"{album_path.name.lower()}.sha256"
    for m in manifests:
        if m.name.lower() == target_name:
            return m

    # Default to the first found manifest
    return manifests[0]


def preview_album_registration(
    conn: sqlite3.Connection,
    library_id: int,
    album_path: Union[str, Path],
    manifest_path: Optional[Union[str, Path]] = None,
) -> AlbumRegistrationPreview:
    """Prepares an incremental registration preview for a single album directory.

    Resolves relative path within the library root, discovers the album manifest,
    and checks against the database without altering records or rehashing files.
    """
    # Fetch library root
    cur = conn.execute("SELECT root_path FROM libraries WHERE id = ?", (library_id,))
    lib_row = cur.fetchone()
    if not lib_row:
        raise ValueError(f"Library with id {library_id} does not exist.")

    library_root = Path(lib_row["root_path"]).resolve()
    target_album = Path(album_path).resolve()

    # Determine relative path from library root
    try:
        album_rel_path = str(target_album.relative_to(library_root))
    except ValueError:
        # Album directory is outside library root
        album_rel_path = target_album.name

    # Check if album already exists in the database
    cur = conn.execute(
        "SELECT id, status FROM albums WHERE library_id = ? AND album_rel_path = ?",
        (library_id, album_rel_path),
    )
    existing_alb = cur.fetchone()
    already_registered = existing_alb is not None
    existing_id = existing_alb["id"] if existing_alb else None

    # Discover manifest if not specified
    if manifest_path is None:
        manifest_file = find_album_manifest(target_album)
    else:
        manifest_file = Path(manifest_path)

    if not manifest_file or not manifest_file.exists():
        return AlbumRegistrationPreview(
            library_id=library_id,
            album_dir=target_album,
            album_rel_path=album_rel_path,
            manifest_path=None,
            preview=None,
            is_already_registered=already_registered,
            existing_album_id=existing_id,
            error_message="No .sha256 checksum manifest found in album folder.",
        )

    # Generate import preview
    import_prev = preview_import(
        conn=conn,
        library_id=library_id,
        manifest_path=manifest_file,
        album_rel_path=album_rel_path,
    )

    return AlbumRegistrationPreview(
        library_id=library_id,
        album_dir=target_album,
        album_rel_path=album_rel_path,
        manifest_path=manifest_file,
        preview=import_prev,
        is_already_registered=already_registered,
        existing_album_id=existing_id,
        error_message=None,
    )


def register_album(
    conn: sqlite3.Connection,
    registration_preview: AlbumRegistrationPreview,
    ignore_conflicts: bool = False,
) -> RegistrationResult:
    """Executes the registration of a new album incrementally.

    Does NOT rehash or read previously registered music tracks.
    Transactions guarantee atomicity and audit trails.
    """
    if registration_preview.error_message:
        return RegistrationResult(
            success=False,
            album_id=registration_preview.existing_album_id,
            album_rel_path=registration_preview.album_rel_path,
            imported_count=0,
            skipped_count=0,
            conflict_count=0,
            message=registration_preview.error_message,
        )

    if registration_preview.preview is None:
        return RegistrationResult(
            success=False,
            album_id=registration_preview.existing_album_id,
            album_rel_path=registration_preview.album_rel_path,
            imported_count=0,
            skipped_count=0,
            conflict_count=0,
            message="No manifest preview available to register.",
        )

    if registration_preview.preview.conflict_records and not ignore_conflicts:
        return RegistrationResult(
            success=False,
            album_id=registration_preview.existing_album_id,
            album_rel_path=registration_preview.album_rel_path,
            imported_count=0,
            skipped_count=len(registration_preview.preview.duplicate_records),
            conflict_count=len(registration_preview.preview.conflict_records),
            message=f"Registration blocked: {len(registration_preview.preview.conflict_records)} conflicts detected.",
        )

    # Commit records via importer
    stats = commit_import(
        conn=conn,
        library_id=registration_preview.library_id,
        preview=registration_preview.preview,
        ignore_conflicts=ignore_conflicts,
    )

    # Retrieve album ID
    cur = conn.execute(
        "SELECT id FROM albums WHERE library_id = ? AND album_rel_path = ?",
        (registration_preview.library_id, registration_preview.album_rel_path),
    )
    album_row = cur.fetchone()
    album_id = album_row["id"] if album_row else None

    # Audit log entry for album registration
    with conn:
        conn.execute(
            """INSERT INTO audit_log (action, details) VALUES (?, ?)""",
            (
                "register_album",
                f"Registered album '{registration_preview.album_rel_path}': "
                f"{stats['imported']} tracks/files added, {stats['skipped']} skipped duplicates.",
            ),
        )

    return RegistrationResult(
        success=True,
        album_id=album_id,
        album_rel_path=registration_preview.album_rel_path,
        imported_count=stats["imported"],
        skipped_count=stats["skipped"],
        conflict_count=stats["conflicts"],
        message="Album successfully registered without re-hashing.",
    )
