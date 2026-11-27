# GEMINI.md - Music Library Integrity Manager Project Instructions

## Project Constraints & Safety Rules
1. **Never modify, delete, rename, or move audio files.** Operations are strictly read-only on music libraries.
2. **Never overwrite existing registered checksum records silently.** Baseline changes require explicit confirmation.
3. **No automatic baseline acceptance.** Discrepancies (changed, missing, unregistered) are reported, never auto-healed.
4. **Offline first.** No external network access, web services, or cloud synchronization.
5. **No unnecessary re-hashing.** Master manifest generation must be derived from SQLite, not by re-reading or re-hashing audio files.
6. **Quota & Complexity Management:**
   - Develop incrementally across defined phases.
   - Work strictly within the current phase scope.
   - Do not refactor stable modules without cause.
   - Rely on automated tests (`pytest`) for regression verification.
   - Maintain concise handoff notes in `PROGRESS.md` after every step.
