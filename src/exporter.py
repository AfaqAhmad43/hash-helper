"""Master Manifest Generation module for Music Library Integrity Manager.

Generates consolidated SHA-256 manifests strictly derived from SQLite
records without reading or re-hashing physical audio files.
"""

import os
import sqlite3
from dataclasses import dataclass
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


def generate_master_manifest(
    conn: sqlite3.Connection,
    library_id: int,
    output_path: Union[str, Path],
    overwrite: bool = False,
    include_companion_files: bool = True,
    library_prefix: Optional[str] = None,
) -> ExportReport:
    """Exports a consolidated SHA-256 master manifest from SQLite.

    Format conforms to RapidCRC specifications:
        <sha256> *<relative_path>
    Line endings: CRLF (\r\n)
    Character encoding: UTF-8 without BOM

    Safety rules:
    - Never reads or hashes physical audio files.
    - Fails if output_path exists and overwrite is False.
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
    duplicates_detected = 0
    malformed_records = 0

    for row in rows:
        alb_rel = row["album_rel_path"]
        f_rel = row["rel_path"]
        sha = row["sha256"].lower().strip()

        if len(sha) != 64:
            malformed_records += 1
            continue

        # Format relative path
        if alb_rel == "." or alb_rel == "":
            full_rel = f_rel
        elif f_rel.startswith(alb_rel):
            full_rel = f_rel
        else:
            full_rel = os.path.join(alb_rel, f_rel)

        # Apply optional library prefix (e.g., 'Organized' folder in master manifest)
        if library_prefix:
            full_rel = os.path.join(library_prefix, full_rel)

        # Normalize to Windows backslashes for RapidCRC compatibility
        norm_path = full_rel.replace("/", "\\")

        # Duplicate check on exported paths
        if norm_path.lower() in seen_paths:
            duplicates_detected += 1
            continue

        seen_paths.add(norm_path.lower())
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

    return ExportReport(
        success=True,
        target_path=target,
        records_exported=len(lines),
        duplicates_detected=duplicates_detected,
        malformed_records=malformed_records,
        message=f"Successfully exported {len(lines)} records to {target.name}.",
    )
