# Music Library Integrity Manager

A lightweight, offline-first Windows desktop tool for managing, maintaining, and verifying the integrity of large FLAC music libraries using SHA-256 checksums and SQLite.

## Purpose
Replaces tedious manual maintenance of consolidated SHA-256 manifests (previously maintained via RapidCRC) with an incremental, database-driven workflow. New albums can be registered without re-hashing previously registered music.

## Features (Planned)
- Import existing RapidCRC album checksum manifests.
- Maintain a local SQLite baseline of registered albums and tracks.
- Incremental album registration without full library re-hashes.
- Instant consolidated master SHA-256 manifest generation.
- Integrity verification (per-album, selected, or full library).
- Library scanner for detecting unregistered and missing files.
- Backup and restore of the integrity database.
- Completely offline operation with standalone packaging.

## Technology Stack
- **Language:** Python 3.10+
- **GUI:** PySide6 (Phase 8)
- **Database:** SQLite
- **Testing:** pytest
- **Packaging:** PyInstaller
