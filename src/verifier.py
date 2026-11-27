"""Verification Engine for Music Library Integrity Manager.

Verifies physical files against the SQLite baseline across album,
selected, or library scopes. Distinguishes verified, changed, missing,
unreadable, and invalid_path files. Verification NEVER modifies the baseline.
"""

import hashlib
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Set, Union

CHUNK_SIZE = 64 * 1024  # 64 KB read buffer
VALID_SCOPES = {"library", "album", "selected"}


def calculate_file_sha256(
    file_path: Union[str, Path],
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Optional[str]:
    """Calculates SHA-256 hash of a file using chunked binary reading.

    Supports mid-file cancellation via cancel_check callable.
    Returns None if cancelled during hashing.
    """
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(CHUNK_SIZE):
            if cancel_check and cancel_check():
                return None
            hasher.update(chunk)
    return hasher.hexdigest().lower()


@dataclass
class FileVerificationResult:
    rel_path: str
    album_rel_path: str
    expected_sha256: str
    actual_sha256: Optional[str]
    status: str  # 'verified', 'changed', 'missing', 'unreadable', 'invalid_path', 'cancelled'
    error_message: Optional[str] = None


@dataclass
class VerificationSummary:
    scope: str
    total_files_in_scope: int
    files_checked: int
    files_matched: int
    files_changed: int
    files_missing: int
    files_unreadable: int
    files_invalid_path: int
    results: List[FileVerificationResult] = field(default_factory=list)
    was_cancelled: bool = False


def validate_scope_and_albums(
    conn: sqlite3.Connection,
    library_id: int,
    scope: str,
    album_ids: Optional[List[int]],
) -> List[int]:
    """Validates verification scope and album IDs strictly.

    Raises ValueError on invalid scope, missing IDs, or cross-library IDs.
    Returns list of verified album IDs (empty list for 'library' scope).
    """
    if scope not in VALID_SCOPES:
        raise ValueError(f"Invalid verification scope '{scope}'. Must be one of: {sorted(VALID_SCOPES)}")

    if scope == "album":
        if not album_ids or len(album_ids) != 1:
            raise ValueError("Scope 'album' requires exactly one valid album ID.")
    elif scope == "selected":
        if not album_ids or len(album_ids) == 0:
            raise ValueError("Scope 'selected' requires a non-empty list of album IDs.")

    if album_ids:
        placeholders = ",".join("?" for _ in album_ids)
        cur = conn.execute(
            f"SELECT id FROM albums WHERE library_id = ? AND id IN ({placeholders})",
            (library_id, *album_ids),
        )
        found_ids = {row["id"] for row in cur.fetchall()}
        missing_ids = set(album_ids) - found_ids
        if missing_ids:
            raise ValueError(f"Album IDs {sorted(missing_ids)} do not belong to library {library_id}.")
        return list(album_ids)

    return []


def verify_files(
    conn: sqlite3.Connection,
    library_id: int,
    scope: str = "library",  # 'library', 'album', or 'selected'
    album_ids: Optional[List[int]] = None,
    progress_callback: Optional[Callable[[int, int, str], bool]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> VerificationSummary:
    """Verifies files on disk against the registered baseline in SQLite.

    Guarantees:
    - Path containment inside library_root.
    - Strict scope validation.
    - Chunked hashing with mid-file cancellation support.
    - Zero modification of baseline or physical files.
    """
    # 1. Fetch and resolve library root path
    cur = conn.execute("SELECT root_path FROM libraries WHERE id = ?", (library_id,))
    lib_row = cur.fetchone()
    if not lib_row:
        raise ValueError(f"Library with id {library_id} does not exist.")

    library_root = Path(lib_row["root_path"]).resolve()

    # 2. Validate scope and album IDs
    valid_album_ids = validate_scope_and_albums(conn, library_id, scope, album_ids)

    # 3. Build file query
    query = """
        SELECT f.id, f.rel_path, f.sha256, a.id as album_id, a.album_rel_path
        FROM file_records f
        JOIN albums a ON f.album_id = a.id
        WHERE a.library_id = ?
    """
    params = [library_id]

    if scope in ("album", "selected") and valid_album_ids:
        placeholders = ",".join("?" for _ in valid_album_ids)
        query += f" AND a.id IN ({placeholders})"
        params.extend(valid_album_ids)

    query += " ORDER BY a.album_rel_path ASC, f.rel_path ASC"

    cur = conn.execute(query, tuple(params))
    records = cur.fetchall()

    total_records = len(records)
    results: List[FileVerificationResult] = []
    matched = 0
    changed = 0
    missing = 0
    unreadable = 0
    invalid_path_count = 0
    was_cancelled = False

    def is_cancelled() -> bool:
        return bool(cancel_check and cancel_check())

    for idx, rec in enumerate(records, start=1):
        rel_path = rec["rel_path"]
        alb_rel = rec["album_rel_path"]
        expected_sha = rec["sha256"].lower()

        # Cancellation check before starting file
        if is_cancelled():
            was_cancelled = True
            break

        if progress_callback:
            continue_processing = progress_callback(idx - 1, total_records, rel_path)
            if continue_processing is False:
                was_cancelled = True
                break

        # 4. Resolve path with strict library root containment
        try:
            # Construct candidate path relative to album directory
            if alb_rel == "." or alb_rel == "":
                candidate_path = (library_root / Path(rel_path)).resolve()
            else:
                candidate_path = (library_root / Path(alb_rel) / Path(rel_path)).resolve()

            # Enforce strict containment within library_root
            candidate_path.relative_to(library_root)
        except (ValueError, RuntimeError) as err:
            # Path escaped library root or contained illegal elements
            invalid_path_count += 1
            results.append(
                FileVerificationResult(
                    rel_path=rel_path,
                    album_rel_path=alb_rel,
                    expected_sha256=expected_sha,
                    actual_sha256=None,
                    status="invalid_path",
                    error_message=f"Path resolves outside library root: {err}",
                )
            )
            continue

        disk_path = candidate_path

        # 5. Check existence
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

        # 6. Compute hash with in-chunk cancellation
        try:
            actual_sha = calculate_file_sha256(disk_path, cancel_check=is_cancelled)
            if actual_sha is None:
                # Cancelled mid-file
                was_cancelled = True
                results.append(
                    FileVerificationResult(
                        rel_path=rel_path,
                        album_rel_path=alb_rel,
                        expected_sha256=expected_sha,
                        actual_sha256=None,
                        status="cancelled",
                        error_message="Verification cancelled by user during hashing.",
                    )
                )
                break

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

    # 7. Record verification session in verification_history and audit_log
    primary_album_id = valid_album_ids[0] if (scope == "album" and valid_album_ids) else None
    overall_status = "cancelled" if was_cancelled else (
        "clean" if (changed == 0 and missing == 0 and unreadable == 0 and invalid_path_count == 0)
        else "discrepancies_detected"
    )

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
                f"Scope='{scope}'. Checked: {len(results)}/{total_records}, Matched: {matched}, "
                f"Changed: {changed}, Missing: {missing}, Unreadable: {unreadable}, "
                f"Invalid: {invalid_path_count}. Status: {overall_status}.",
            ),
        )

    return VerificationSummary(
        scope=scope,
        total_files_in_scope=total_records,
        files_checked=len(results),
        files_matched=matched,
        files_changed=changed,
        files_missing=missing,
        files_unreadable=unreadable,
        files_invalid_path=invalid_path_count,
        results=results,
        was_cancelled=was_cancelled,
    )
