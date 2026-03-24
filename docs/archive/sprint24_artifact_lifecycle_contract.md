# Sprint 24 — Artifact Lifecycle Management Surface

**Status: ✅ Implemented**
**Sprint Date: 2026-03-20**
**Tests: 975 total (26 new: 21 unit + 5 CLI)**

---

## Overview

Sprint 24 closes the operational loop established in Sprints 21–23.

- Sprint 21: detect stale artifacts (readiness report)
- Sprint 22: observe provider health and distribution drift
- Sprint 23: advisory protective gate recommendations (including stale artifact warnings)
- **Sprint 24: surface artifact inventory + archive stale artifacts (operator-triggered, never automatic)**

The core principle remains: **KAI is observational. No auto-remediation.**
`artifact-rotate` is a controlled operator action, not an autonomous cleanup process.

---

## Core Principle (I-146)

`app/research/artifact_lifecycle.py` is the sole canonical artifact lifecycle management module.
No second artifact management stack is permitted.

---

## Invariants (I-146–I-152)

| ID | Rule |
|----|------|
| I-146 | `artifact_lifecycle.py` is the sole canonical artifact lifecycle management layer. No second stack. |
| I-147 | `rotate_stale_artifacts()` MUST default to `dry_run=True`. No filesystem writes when `dry_run=True`. |
| I-148 | Rotation archives to `artifacts/archive/<YYYYMMDD_HHMMSS>/` ONLY. Never deletes, never overwrites source files. |
| I-149 | `get_artifact_inventory` MCP tool is strictly read-only. No filesystem mutations. |
| I-150 | `ArtifactInventoryReport.execution_enabled` MUST always be `False`. |
| I-151 | Stale detection uses file `mtime` only — no content inspection of artifact files. |
| I-152 | CLI `artifact-rotate` defaults to `--dry-run`. Operator must pass `--no-dry-run` for actual archival. |

---

## Canonical Module

**`app/research/artifact_lifecycle.py`**

### Data Models

| Type | Description |
|------|-------------|
| `ArtifactEntry` | Single artifact file: name, path (rel), size_bytes, modified_at (ISO), age_days, status |
| `ArtifactInventoryReport` | Read-only snapshot: entries, stale_count, current_count, total_size_bytes, execution_enabled=False |
| `ArtifactRotationSummary` | Rotation result: archived_count, skipped_count, archived_paths, dry_run, archive_dir |

### Functions

| Function | Description |
|----------|-------------|
| `build_artifact_inventory(artifacts_dir, stale_after_days=30.0)` | Scan top-level `.json`/`.jsonl` files; exclude `archive/` subdir |
| `rotate_stale_artifacts(artifacts_dir, stale_after_days=30.0, *, dry_run=True)` | Archive stale files; dry_run=True means no writes |
| `save_artifact_inventory(report, path)` | Persist inventory report as JSON |
| `save_artifact_rotation_summary(summary, path)` | Persist rotation summary as JSON |

### Managed File Types

Only files with `.json` or `.jsonl` suffixes are inventoried/rotated.
Directories (including `archive/`) are always skipped.
Unmanaged files (`.txt`, `.db`, etc.) are never touched.

---

## MCP Surface (read-only)

**`get_artifact_inventory(artifacts_dir, stale_after_days)`**

- Read-only tool (I-149)
- Returns: `ArtifactInventoryReport.to_json_dict()` with `execution_enabled=False`
- Workspace-confined via `_resolve_workspace_dir()` (I-95)
- No filesystem mutations of any kind

---

## CLI Surface

**`research artifact-inventory [--artifacts-dir DIR] [--stale-after-days N] [--out FILE]`**

- Lists all managed artifacts with age and status (current/stale)
- Prints summary: total, current count, stale count, total size
- `--out` saves the JSON inventory report

**`research artifact-rotate [--artifacts-dir DIR] [--stale-after-days N] [--dry-run/--no-dry-run] [--out FILE]`**

- Default: `--dry-run` (I-152) — prints what WOULD be archived, no writes
- `--no-dry-run`: actually moves stale files to `artifacts/archive/<timestamp>/`
- Files are NEVER deleted (I-148)
- `--out` saves the rotation summary JSON

---

## Archive Contract

- Archive location: `artifacts/archive/YYYYMMDD_HHMMSS/` (one subdir per rotation run)
- Archive is a subdirectory of the managed artifacts directory (workspace-confined)
- Files are moved (`shutil.move`), never deleted, never overwritten
- The `archive/` directory itself is never inventoried or rotated

---

## What is Explicitly Excluded

- No automatic/scheduled rotation (must be operator-triggered via CLI)
- No deletion of any artifact file (move-only, I-148)
- No content inspection of artifact files (mtime-based only, I-151)
- No write-back to CanonicalDocument, route state, or signal handoffs
- No trading semantics, no execution enablement
- No second artifact management stack
