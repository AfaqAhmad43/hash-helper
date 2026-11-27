# Technical Notes

## Checksum Manifest Format Analysis (RapidCRC)

Based on direct inspection of `Organized.sha256` (4,443 records) and individual album `.sha256` files in `sample_data/manifests/`:

### 1. File Format & Syntax
- **Hash Algorithm:** SHA-256 (64 hexadecimal characters, lowercase).
- **Delimiter Format:** GNU/coreutils binary mode delimiter: ` *` (space followed by asterisk).
  - Format: `<64-char-hex-hash> *<filepath>`
  - Example (Album manifest): `35db280173d0969822fab68db705468cacca3c746f80972a52f9f539d229977f *01 - Deep Diving.flac`
  - Example (Master manifest): `f525c944cfd2df6baf48755bbeff3c4a20bc03f4e54badb988ca9eed59026ffd *Organized\Adeem The Artist\[2022] White Trash Revelry [WEB - Qobuz]\[2022] White Trash Revelry [WEB - Qobuz].par2`
- **Line Endings:** Windows CRLF (`\r\n`).
- **Character Encoding:** UTF-8 without BOM. Decodes cleanly with standard UTF-8.

### 2. Path Conventions
- **Path Separator:** Windows backslash (`\`).
- **Album-Level Manifests:** Paths are relative to the folder containing the manifest (e.g. `*01 - Track.flac` or `*CD1\01 - Track.flac`).
- **Master Manifest:** Paths are relative to the directory containing the master manifest file (or the library root), including top-level directory names (e.g., `*Organized\Artist\Album\file.flac`).

### 3. File Types Observed in Manifests
The manifest contains non-audio accompanying files along with FLAC tracks:
- **Audio:** `.flac` (primary music tracks: 2,388 entries in sample master manifest)
- **Parity Files:** `.par2` (647 entries)
- **Artwork:** `.jpg`, `.jpeg`, `.png`, `.pdf` (565 entries)
- **Metadata & Logs:** `.cue`, `.log`, `.txt`, `.accurip`, `.nfo` (543 entries)
- **Checksums & Playlists:** `.sha256`, `.m3u` (300 entries)

### 4. Architectural Decisions for Import & Database
- **Parsing:** Split pattern `^([a-fA-F0-9]{64}) \*(.+)$` safely extracts the hash and relative path.
- **Normalization:** In SQLite, paths should be stored canonically. When exporting back for RapidCRC, paths will be formatted with Windows backslashes (`\`) and ` *` delimiter.
- **File Scope:** Because manifests track both music tracks (`.flac`) and album companion files (`.cue`, artwork, `.log`, `.par2`), the database schema should distinguish or flexibly track both tracks and companion files under an album.
