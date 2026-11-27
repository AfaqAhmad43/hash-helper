"""Verification Engine for Music Library Integrity Manager.

Verifies physical files against the SQLite baseline across album,
selected, or library scopes. Distinguishes verified, changed, missing,
and unreadable files. Verification NEVER modifies the baseline.
"""

import hashlib
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Set, Union

CHUNK_SIZE = 64 * 1024  # 64 KB read buffer


def calculate_file_sha256(file_path: Union[str, Path]) -> str:
    """Calculates SHA-256 hash of a file using chunked binary reading."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(CHUNK_SIZE):
            hasher.update(chunk)
    return hasher.hexdigest().lower()


@dataclass
class FileVerificationResult:
    rel_path: str
    album_rel_path: str
    expected_sha256: str
    actual_sha256: Optional[str]
    status: str  # 'verified', 'changed', 'missing', 'unreadable'
    error_message: Optional[str] = None


@dataclass
class VerificationSummary:
    scope: str
    files_checked: int
    files_matched: int
    files_changed: int
    files_missing: int
    files_unreadable: int
    results: List[FileVerificationResult]
    was_cancelled: bool = False


def verify_files(
    conn: sqlite3.Connection,
    library_id: int,
    scope: str = "library",  # 'library', 'album', or 'selected'
    album_ids: Optional[List[int]] = None,
    progress_callback: Optional[Callable[[int, int, str], bool]] = None,
) -> VerificationSummary:
    """Verifies files on disk against the registered baseline in SQLite.

    Args:
        conn: SQLite connection
        library_id: Target library ID
        scope: 'library', 'album', or 'selected'
        album_ids: List of album IDs if scope is 'album' or 'selected'
        progress_callback: Optional callback(current, total, file_path) -> bool.
                           Returning False signals cancellation.

    Safety: Never modifies or updates the baseline.
    """
    # Fetch library root path
    cur = conn.execute("SELECT root_path FROM libraries WHERE id = ?", (library_id,))
    lib_row = cur.fetchone()
    if not lib_row:
        raise ValueError(f"Library with id {library_id} does not exist.")

    library_root = Path(lib_row["root_path"]).resolve()

    # Build file query
    query = """
        SELECT f.id, f.rel_path, f.sha256, a.id as album_id, a.album_rel_path
        FROM file_records f
        JOIN albums a ON f.album_id = a.id
        WHERE a.library_id = ?
    """
    params = [library_id]

    if scope in ("album", "selected") and album_ids:
        placeholders = ",".join("?" for _ in album_ids)
        query += f" AND a.id IN ({placeholders})"
        params.extend(album_ids)

    query += " ORDER BY a.album_rel_path ASC, f.rel_path ASC"

    cur = conn.execute(query, tuple(params))
    records = cur.fetchall()

    total_records = len(records)
    results: List[FileVerificationResult] = []
    matched = 0
    changed = 0
    missing = 0
    unreadable = 0
    was_cancelled = False

    for idx, rec in enumerate(records, start=1):
        rel_path = rec["rel_path"]
        alb_rel = rec["album_rel_path"]
        expected_sha = rec["sha256"].lower()

        # Cancellation check before starting file
        if progress_callback:
            continue_processing = progress_callback(idx - 1, total_records, rel_path)
            if continue_processing is False:
                was_cancelled = True
                break

        # Resolve path on disk
        if alb_rel == "." or alb_rel == "":
            disk_path = library_root / rel_path
        elif rel_path.startswith(alb_rel):
            disk_path = library_root / rel_path
        else:
            disk_path = library_root / alb_rel / rel_path

        if not disk_path.exists():
            missing += 1
            results.append(
                FileVerificationResult(
                    rel_path=rel_path,
                    album_rel_path=alb_rel,
                    expected_sha256=expected_sha,
                    actual_sha256=None,
                    status="missing",
                    error_message="File not found on disk.",
                )
            )
            continue

        try:
            actual_sha = calculate_file_sha256(disk_path)
            if actual_sha == expected_sha:
                matched += 1
                status = "verified"
                err = None
            else:
                changed += 1
                status = "changed"
                err = "Checksum does not match registered baseline."

            results.append(
                FileVerificationResult(
                    rel_path=rel_path,
                    album_rel_path=alb_rel,
                    expected_sha256=expected_sha,
                    actual_sha256=actual_sha,
                    status=status,
                    error_message=err,
                )
            )
        except (IOError, OSError, PermissionError) as ex:
            unreadable += 1
            results.append(
                FileVerificationResult(
                    rel_path=rel_path,
                    album_rel_path=alb_rel,
                    expected_sha256=expected_sha,
                    actual_sha256=None,
                    status="unreadable",
                    error_message=str(ex),
                )
            )

    # Persist verification session into verification_history
    primary_album_id = album_ids[0] if (scope == "album" and album_ids) else None
    overall_status = "cancelled" if was_cancelled else ("clean" if (changed == 0 and missing == 0 and unreadable == 0) else "discrepancies_detected")

    with conn:
        conn.execute(
            """INSERT INTO verification_history (
                   library_id, album_id, scope, files_checked, files_matched,
                   files_changed, files_missing, files_unreadable, status
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                library_id,
                primary_album_id,
                scope,
                len(results),
                matched,
                changed,
                missing,
                unreadable,
                overall_status,
            ),
        )

        conn.execute(
            """INSERT INTO audit_log (action, details) VALUES (?, ?)""",
            (
                "verify_integrity",
                f"Verification scope='{scope}'. Checked: {len(results)}, Matched: {matched}, "
                f"Changed: {changed}, Missing: {missing}, Unreadable: {unreadable}. Status: {overall_status}.",
            ),
        )

    return VerificationSummary(
        scope=scope,
        files_checked=len(results),
        files_matched=matched,
        files_changed=changed,
        files_missing=missing,
        files_unreadable=unreadable,
        results=results,
        was_cancelled=was_cancelled,
    )
