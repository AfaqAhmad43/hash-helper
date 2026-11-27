# Music Library Integrity Manager

A lightweight, offline-first Windows desktop tool for managing, maintaining, and verifying the integrity of large FLAC music libraries using SHA-256 checksums and SQLite.

## Purpose
Replaces tedious manual maintenance of consolidated SHA-256 manifests (previously maintained via RapidCRC) with an incremental, database-driven workflow. New albums can be registered without re-hashing previously registered music.

## How to Run

### Option 1: Standalone Windows Executable (No Python Required)
Run the compiled standalone executable:
```cmd
dist\MusicIntegrityManager.exe
```

### Option 2: Running from Source
1. Activate virtual environment:
   ```cmd
   .\venv\Scripts\activate
   ```
2. Install dependencies:
   ```cmd
   pip install -r requirements-dev.txt
   ```
3. Run the desktop application:
   ```cmd
   python main.py
   ```
4. Run automated test suite:
   ```cmd
   pytest
   ```
