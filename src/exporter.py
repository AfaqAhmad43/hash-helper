"""Master Manifest Generation module for Music Library Integrity Manager.

Generates consolidated SHA-256 manifests strictly derived from SQLite
records without reading or re-hashing physical audio files.
"""

import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Set, Union


@dataclass
class ExportReport:
    success: bool
    target_path: Path
    records_exported: int
    duplicates_detected: int
    malformed_records: int
    message: str
    errors: List[str] = field(default_factory=list)


def generate_master_manifest(
    conn: sqlite3.Connection,
    library_id: int,
    output_path: Union[str, Path],
    overwrite: bool = False,
    include_companion_files: bool = True,
    library_prefix: Optional[str] = None,
) -> ExportReport:
    """Exports a consolidated SHA-256 master manifest from SQLite.

    Format conforms strictly to RapidCRC specifications:
        <sha256> *<relative_path>
    Line endings: CRLF (\r\n)
    Character encoding: UTF-8 without BOM

    Safety rules:
    - Never reads or hashes physical audio files.
    - Strictly fails if output_path exists and overwrite is False.
    - Validates paths using proper Path objects instead of string slicing.
    - Reports malformed records and duplicate paths without silent data corruption.
    """
    target = Path(output_path).resolve()

    if target.exists() and not overwrite:
        return ExportReport(
            success=False,
            target_path=target,
            records_exported=0,
            duplicates_detected=0,
            malformed_records=0,
            message=f"Export destination already exists and overwrite=False: {target}",
            errors=["Destination file already exists."],
        )

    # Query records from database
    query = """
        SELECT a.album_rel_path, f.rel_path, f.sha256, f.file_type
        FROM file_records f
        JOIN albums a ON f.album_id = a.id
        WHERE a.library_id = ?
    """
    params = [library_id]
    if not include_companion_files:
        query += " AND f.file_type = 'audio'"

    query += " ORDER BY a.album_rel_path ASC, f.rel_path ASC"

    cur = conn.execute(query, tuple(params))
    rows = cur.fetchall()

    lines: List[str] = []
    seen_paths: Set[str] = set()
    errors: List[str] = []
    duplicates_detected = 0
    malformed_records = 0

    for row in rows:
        alb_rel = row["album_rel_path"]
        f_rel = row["rel_path"]
        sha = row["sha256"].lower().strip()

        # Validate hash format (64-character lowercase hex)
        if len(sha) != 64 or not all(c in "0123456789abcdef" for c in sha):
            malformed_records += 1
            errors.append(f"Malformed SHA-256 for record '{alb_rel}/{f_rel}': '{sha}'")
            continue

        # Deterministic path resolution using Path objects
        alb_path_obj = Path(alb_rel) if alb_rel not in (".", "") else Path("")
        f_path_obj = Path(f_rel)

        # Build clean relative path
        if alb_path_obj == Path("") or f_path_obj.parts[: len(alb_path_obj.parts)] == alb_path_obj.parts:
            combined_rel = f_path_obj
        else:
            combined_rel = alb_path_obj / f_path_obj

        # Apply optional library prefix
        if library_prefix:
            combined_rel = Path(library_prefix) / combined_rel

        # Format with Windows backslashes for RapidCRC
        norm_path = str(combined_rel).replace("/", "\\")

        # Duplicate check on exported paths
        norm_key = norm_path.lower()
        if norm_key in seen_paths:
            duplicates_detected += 1
            errors.append(f"Duplicate path in export: '{norm_path}'")
            continue

        seen_paths.add(norm_key)
        lines.append(f"{sha} *{norm_path}")

    # Ensure parent directory exists
    target.parent.mkdir(parents=True, exist_ok=True)

    # Write file using CRLF and UTF-8
    content = "\r\n".join(lines)
    if content:
        content += "\r\n"

    target.write_bytes(content.encode("utf-8"))

    # Log to audit log
    with conn:
        conn.execute(
            """INSERT INTO audit_log (action, details) VALUES (?, ?)""",
            (
                "export_master_manifest",
                f"Exported {len(lines)} records to {target.name}. "
                f"Duplicates: {duplicates_detected}, Malformed: {malformed_records}.",
            ),
        )

    msg = f"Successfully exported {len(lines)} records to {target.name}."
    if duplicates_detected > 0 or malformed_records > 0:
        msg += f" Note: {duplicates_detected} duplicates skipped, {malformed_records} malformed records omitted."

    return ExportReport(
        success=True,
        target_path=target,
        records_exported=len(lines),
        duplicates_detected=duplicates_detected,
        malformed_records=malformed_records,
        message=msg,
        errors=errors,
    )
