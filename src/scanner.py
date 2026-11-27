"""Library Scanner module for Music Library Integrity Manager.

Scans the physical music directory to detect discrepancies against
the SQLite database baseline (unregistered files, missing files, new albums).
The scanner NEVER modifies physical files or the database baseline.
"""

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Union

from src.importer import AUDIO_EXTENSIONS, COMPANION_EXTENSIONS, MANIFEST_EXTENSIONS, classify_file_type
from src.registration import find_album_manifest


@dataclass
class PotentialAlbum:
    album_rel_path: str
    manifest_found: Optional[str]
    audio_files_count: int
    companion_files_count: int


@dataclass
class ScanReport:
    total_physical_files: int
    registered_files_count: int
    unregistered_files: List[str]
    missing_registered_files: List[str]
    potential_new_albums: List[PotentialAlbum]
    was_cancelled: bool = False


def scan_library(
    conn: sqlite3.Connection,
    library_id: int,
    progress_callback: Optional[Callable[[int, str], bool]] = None,
) -> ScanReport:
    """Scans physical filesystem and compares against SQLite baseline.

    Detects:
    - Unregistered physical files
    - Missing registered files
    - Potential new album folders

    Safety:
    - Purely read-only; calculates NO hashes and updates NO records.
    """
    cur = conn.execute("SELECT root_path FROM libraries WHERE id = ?", (library_id,))
    lib_row = cur.fetchone()
    if not lib_row:
        raise ValueError(f"Library with id {library_id} does not exist.")

    library_root = Path(lib_row["root_path"]).resolve()

    # Query registered database files
    cur = conn.execute(
        """SELECT a.album_rel_path, f.rel_path
           FROM file_records f
           JOIN albums a ON f.album_id = a.id
           WHERE a.library_id = ?""",
        (library_id,),
    )
    db_rows = cur.fetchall()

    # Map registered normalized paths
    registered_paths: Set[str] = set()
    for row in db_rows:
        alb_rel = row["album_rel_path"]
        f_rel = row["rel_path"]
        if alb_rel == "." or alb_rel == "" or f_rel.startswith(alb_rel):
            full_rel = f_rel
        else:
            full_rel = os.path.join(alb_rel, f_rel)
        registered_paths.add(full_rel.replace("/", "\\").lower())

    # Query registered album relative paths
    cur = conn.execute("SELECT album_rel_path FROM albums WHERE library_id = ?", (library_id,))
    registered_albums: Set[str] = {r["album_rel_path"].replace("/", "\\").lower() for r in cur.fetchall()}

    # Enumerate physical directory
    physical_files_count = 0
    unregistered_files: List[str] = []
    seen_physical_paths: Set[str] = set()
    album_candidates: Dict[str, Dict[str, int]] = {}
    was_cancelled = False

    if library_root.exists() and library_root.is_dir():
        for root, dirs, files in os.walk(library_root):
            rel_dir = os.path.relpath(root, library_root)
            norm_rel_dir = "" if rel_dir == "." else rel_dir.replace("/", "\\")

            for f in files:
                physical_files_count += 1
                norm_f_rel = os.path.join(norm_rel_dir, f) if norm_rel_dir else f
                norm_key = norm_f_rel.replace("/", "\\").lower()
                seen_physical_paths.add(norm_key)

                if progress_callback:
                    if progress_callback(physical_files_count, norm_f_rel) is False:
                        was_cancelled = True
                        break

                ftype = classify_file_type(f)

                # Check if file is registered in database
                if norm_key not in registered_paths:
                    unregistered_files.append(norm_f_rel)

                # Track stats for album folder candidate
                if norm_rel_dir:
                    alb_key = norm_rel_dir.lower()
                    if alb_key not in album_candidates:
                        album_candidates[alb_key] = {
                            "path": norm_rel_dir,
                            "audio": 0,
                            "companion": 0,
                        }
                    if ftype == "audio":
                        album_candidates[alb_key]["audio"] += 1
                    else:
                        album_candidates[alb_key]["companion"] += 1

            if was_cancelled:
                break

    # Determine missing registered files
    missing_registered: List[str] = []
    for reg in registered_paths:
        if reg not in seen_physical_paths:
            missing_registered.append(reg)

    # Detect potential new album folders (folders containing audio/companion not registered)
    potential_albums: List[PotentialAlbum] = []
    for alb_key, data in album_candidates.items():
        if alb_key not in registered_albums and data["audio"] > 0:
            alb_dir = library_root / data["path"]
            manifest = find_album_manifest(alb_dir)
            potential_albums.append(
                PotentialAlbum(
                    album_rel_path=data["path"],
                    manifest_found=manifest.name if manifest else None,
                    audio_files_count=data["audio"],
                    companion_files_count=data["companion"],
                )
            )

    return ScanReport(
        total_physical_files=physical_files_count,
        registered_files_count=len(registered_paths),
        unregistered_files=unregistered_files,
        missing_registered_files=missing_registered,
        potential_new_albums=potential_albums,
        was_cancelled=was_cancelled,
    )
