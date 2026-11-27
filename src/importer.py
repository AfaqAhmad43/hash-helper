"""Manifest parsing and import management for RapidCRC manifests."""

import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

SHA256_LINE_REGEX = re.compile(r"^([a-fA-F0-9]{64}) \*(.+)$")

AUDIO_EXTENSIONS = {".flac", ".mp3", ".m4a", ".wav", ".ogg", ".opus", ".alac", ".aiff"}
COMPANION_EXTENSIONS = {".cue", ".log", ".txt", ".accurip", ".nfo", ".jpg", ".jpeg", ".png", ".pdf", ".par2"}
MANIFEST_EXTENSIONS = {".sha256", ".md5", ".m3u", ".m3u8"}


def classify_file_type(file_path: str) -> str:
    """Classifies file as 'audio', 'companion', 'manifest', or 'other' based on extension."""
    ext = os.path.splitext(file_path)[1].lower()
    if ext in AUDIO_EXTENSIONS:
        return "audio"
    if ext in COMPANION_EXTENSIONS:
        return "companion"
    if ext in MANIFEST_EXTENSIONS:
        return "manifest"
    return "other"


@dataclass
class ParsedRecord:
    sha256: str
    rel_path: str  # Path as written in the manifest
    file_type: str
    line_number: int


@dataclass
class ImportItem:
    album_rel_path: str
    file_rel_path: str
    sha256: str
    file_type: str
    status: str  # 'new', 'duplicate', 'conflict'
    existing_sha256: Optional[str] = None


@dataclass
class ImportPreview:
    source_manifest: str
    manifest_mtime: float
    manifest_size: int
    total_parsed: int
    new_records: List[ImportItem]
    duplicate_records: List[ImportItem]
    conflict_records: List[ImportItem]
    malformed_lines: List[Tuple[int, str]]


def parse_manifest_file(manifest_path: Union[str, Path]) -> Tuple[List[ParsedRecord], List[Tuple[int, str]]]:
    """Parses a RapidCRC .sha256 manifest file.

    Returns:
        (valid_records, malformed_lines)
    """
    manifest_path = Path(manifest_path)
    records: List[ParsedRecord] = []
    malformed: List[Tuple[int, str]] = []

    with open(manifest_path, "r", encoding="utf-8", errors="replace") as f:
        for idx, line in enumerate(f, start=1):
            line_str = line.strip()
            if not line_str:
                continue

            match = SHA256_LINE_REGEX.match(line_str)
            if not match:
                malformed.append((idx, line_str))
                continue

            sha256_hash, raw_rel_path = match.groups()
            sha256_hash = sha256_hash.lower()
            # Normalize path slashes to standard platform representation
            norm_path = raw_rel_path.replace("/", "\\")
            ftype = classify_file_type(norm_path)

            records.append(
                ParsedRecord(
                    sha256=sha256_hash,
                    rel_path=norm_path,
                    file_type=ftype,
                    line_number=idx,
                )
            )

    return records, malformed


def preview_import(
    conn: sqlite3.Connection,
    library_id: int,
    manifest_path: Union[str, Path],
    album_rel_path: Optional[str] = None,
) -> ImportPreview:
    """Prepares an import preview by checking parsed records against existing database records.

    If album_rel_path is not provided, it is deduced from the manifest's parent directory
    or relative path structure.
    """
    manifest_path = Path(manifest_path)
    records, malformed = parse_manifest_file(manifest_path)

    new_records: List[ImportItem] = []
    duplicate_records: List[ImportItem] = []
    conflict_records: List[ImportItem] = []

    # Get library root for path resolution if needed
    cur = conn.execute("SELECT root_path FROM libraries WHERE id = ?", (library_id,))
    lib_row = cur.fetchone()
    if not lib_row:
        raise ValueError(f"Library with id {library_id} does not exist.")

    for record in records:
        # Determine album association
        if album_rel_path is not None:
            alb_path = album_rel_path
            # File relative path within album
            file_rel = record.rel_path
        else:
            # If path contains directories, the directory is the album
            dirname = os.path.dirname(record.rel_path)
            alb_path = dirname if dirname else "."
            file_rel = record.rel_path

        # Check existing records in database for this album and file
        cur = conn.execute(
            """SELECT f.sha256
               FROM file_records f
               JOIN albums a ON f.album_id = a.id
               WHERE a.library_id = ? AND a.album_rel_path = ? AND f.rel_path = ?""",
            (library_id, alb_path, file_rel),
        )
        existing = cur.fetchone()

        if existing is None:
            new_records.append(
                ImportItem(
                    album_rel_path=alb_path,
                    file_rel_path=file_rel,
                    sha256=record.sha256,
                    file_type=record.file_type,
                    status="new",
                )
            )
        elif existing["sha256"].lower() == record.sha256.lower():
            duplicate_records.append(
                ImportItem(
                    album_rel_path=alb_path,
                    file_rel_path=file_rel,
                    sha256=record.sha256,
                    file_type=record.file_type,
                    status="duplicate",
                    existing_sha256=existing["sha256"],
                )
            )
        else:
            conflict_records.append(
                ImportItem(
                    album_rel_path=alb_path,
                    file_rel_path=file_rel,
                    sha256=record.sha256,
                    file_type=record.file_type,
                    status="conflict",
                    existing_sha256=existing["sha256"],
                )
            )

    m_stat = manifest_path.stat()

    return ImportPreview(
        source_manifest=str(manifest_path.resolve()),
        manifest_mtime=m_stat.st_mtime,
        manifest_size=m_stat.st_size,
        total_parsed=len(records),
        new_records=new_records,
        duplicate_records=duplicate_records,
        conflict_records=conflict_records,
        malformed_lines=malformed,
    )


def commit_import(
    conn: sqlite3.Connection,
    library_id: int,
    preview: ImportPreview,
    ignore_conflicts: bool = False,
) -> Dict[str, int]:
    """Commits new records from an ImportPreview into the database in a single transaction.

    Safety:
    - Existing records and conflicts are NOT overwritten silently.
    - If conflicts exist and ignore_conflicts is False, raises a ValueError.
    - Verifies manifest has not been modified or deleted since preview generation (stale check).
    """
    manifest_file = Path(preview.source_manifest)
    if not manifest_file.exists():
        raise FileNotFoundError(f"Source manifest was deleted or moved: {preview.source_manifest}")

    current_stat = manifest_file.stat()
    if current_stat.st_mtime != preview.manifest_mtime or current_stat.st_size != preview.manifest_size:
        raise ValueError(
            "Source manifest was modified on disk after preview was generated. "
            "Please regenerate the preview before committing."
        )

    if preview.conflict_records and not ignore_conflicts:
        raise ValueError(
            f"Cannot commit import: {len(preview.conflict_records)} conflicts detected. "
            f"Baseline records cannot be overwritten without explicit approval."
        )

    albums_cache: Dict[str, int] = {}

    with conn:
        for item in preview.new_records:
            alb_rel = item.album_rel_path
            if alb_rel not in albums_cache:
                cur = conn.execute(
                    "SELECT id FROM albums WHERE library_id = ? AND album_rel_path = ?",
                    (library_id, alb_rel),
                )
                alb_row = cur.fetchone()
                if alb_row:
                    alb_id = alb_row["id"]
                else:
                    cur = conn.execute(
                        "INSERT INTO albums (library_id, album_rel_path, display_name) VALUES (?, ?, ?)",
                        (library_id, alb_rel, os.path.basename(alb_rel) or alb_rel),
                    )
                    alb_id = cur.lastrowid
                albums_cache[alb_rel] = alb_id
            else:
                alb_id = albums_cache[alb_rel]

            conn.execute(
                """INSERT INTO file_records (album_id, rel_path, sha256, file_type)
                   VALUES (?, ?, ?, ?)""",
                (alb_id, item.file_rel_path, item.sha256, item.file_type),
            )

        # Record in import_history
        conn.execute(
            """INSERT INTO import_history (library_id, source_manifest, records_imported,
                                          records_skipped, records_conflicted, status)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                library_id,
                preview.source_manifest,
                len(preview.new_records),
                len(preview.duplicate_records),
                len(preview.conflict_records),
                "completed" if not preview.conflict_records else "completed_with_ignored_conflicts",
            ),
        )

        # Audit log
        conn.execute(
            """INSERT INTO audit_log (action, details) VALUES (?, ?)""",
            (
                "import_manifest",
                f"Imported {len(preview.new_records)} records from {os.path.basename(preview.source_manifest)}. "
                f"Skipped {len(preview.duplicate_records)} duplicates. "
                f"Conflicts: {len(preview.conflict_records)}.",
            ),
        )

    return {
        "imported": len(preview.new_records),
        "skipped": len(preview.duplicate_records),
        "conflicts": len(preview.conflict_records),
    }
