"""Artifact lifecycle management — inventory, rotation, and retention policy.

Sprint 24: read-only inventory + dry-run-safe rotation surface.
Sprint 25: safe retention & cleanup policy classification.
Sprint 26/26D: final operational governance/review surfaces for retention,
cleanup eligibility, protected artifacts, and review-required operator flow.

Closes the operational loop established in Sprints 21–23:
  detect stale artifacts (readiness/gates) → classify retention (Sprint 25)
  → surface them (inventory) → archive them (rotate).

Core invariants (I-146–I-152, Sprint 24):
- I-146: This module is the sole canonical artifact lifecycle management layer.
- I-147: rotate_stale_artifacts() defaults to dry_run=True. No filesystem writes when dry-run.
- I-148: Rotation archives to artifacts/archive/<timestamp>/ only. Never deletes, never overwrites.
- I-149: get_artifact_inventory MCP tool is read-only. No mutations.
- I-150: ArtifactInventoryReport.execution_enabled is always False.
- I-151: Stale detection uses file mtime only — no content inspection.
- I-152: CLI artifact-rotate defaults to --dry-run. Operator must pass --no-dry-run.

Core invariants (I-153–I-161, Sprint 25):
- I-153: Retention policy is classification only. No cleanup triggered automatically.
- I-154: ArtifactRetentionEntry.delete_eligible MUST always be False. No platform deletion.
- I-155: protected=True artifacts MUST NOT appear as rotation candidates.
- I-156: AUDIT_TRAIL artifacts always protected (mcp_write_audit, consumer_ack, alert_audit).
- I-157: PROMOTION_RECORD artifacts always protected (promotion_record.json).
- I-158: TRAINING_DATA artifacts always protected (teacher, candidate, tuning_manifest).
- I-159: ACTIVE_STATE artifacts protected when route is active (active_route_profile.json).
- I-160: build_retention_report() is pure computation — no DB, LLM, network, or writes.
- I-161: ArtifactRetentionReport.execution_enabled and write_back_allowed always False.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# File suffixes that are managed as artifacts (logs, reports, manifests)
_MANAGED_SUFFIXES = frozenset({".json", ".jsonl"})

# Subdirectory for rotation archives — must stay inside the managed artifacts dir
ARCHIVE_SUBDIR = "archive"
REVIEW_JOURNAL_JSONL_FILENAME = "operator_review_journal.jsonl"
DECISION_JOURNAL_JSONL_FILENAME = "decision_journal.jsonl"

ARTIFACT_STATUS_CURRENT = "current"
ARTIFACT_STATUS_STALE = "stale"
ARTIFACT_STATUS_UNKNOWN = "unknown"

# ---------------------------------------------------------------------------
# Sprint 25: Retention policy constants
# ---------------------------------------------------------------------------

# Artifact classes — what kind of data this artifact holds
ARTIFACT_CLASS_AUDIT_TRAIL = "audit_trail"
ARTIFACT_CLASS_PROMOTION = "promotion"
ARTIFACT_CLASS_TRAINING_DATA = "training_data"
ARTIFACT_CLASS_ACTIVE_STATE = "active_state"
ARTIFACT_CLASS_EVALUATION = "evaluation"
ARTIFACT_CLASS_OPERATIONAL = "operational"
ARTIFACT_CLASS_UNKNOWN = "unknown"

# Retention classes — what the operator should do with this artifact
RETENTION_CLASS_PROTECTED = "protected"
RETENTION_CLASS_ROTATABLE = "rotatable"
RETENTION_CLASS_REVIEW_REQUIRED = "review_required"

# Known protected filenames by class (I-156, I-157, I-158)
_PROTECTED_AUDIT_FILENAMES: frozenset[str] = frozenset(
    {
        "mcp_write_audit.jsonl",  # MCP write audit trail (I-94)
        "consumer_acknowledgements.jsonl",  # Consumer ack audit trail (I-116)
        "alert_audit.jsonl",  # Alert dispatch audit trail
        REVIEW_JOURNAL_JSONL_FILENAME,  # Operator review journal (Sprint 33)
        DECISION_JOURNAL_JSONL_FILENAME,  # Decision journal (Sprint 34)
    }
)
_PROTECTED_AUDIT_NAME_MARKERS: frozenset[str] = frozenset(
    {
        "signal_handoff",
        "execution_handoff",
        "execution_signal_handoff",
    }
)
_PROTECTED_PROMOTION_FILENAMES: frozenset[str] = frozenset(
    {
        "promotion_record.json",  # Promotion gate record (I-43/I-45)
    }
)
_PROTECTED_TRAINING_FILENAMES: frozenset[str] = frozenset(
    {
        "teacher.jsonl",  # Teacher training corpus (I-19)
        "candidate.jsonl",  # Candidate dataset (I-29)
        "tuning_manifest.json",  # Tuning artifact (I-40)
    }
)
_ACTIVE_STATE_FILENAMES: frozenset[str] = frozenset(
    {
        "active_route_profile.json",  # Runtime route activation state (I-90)
    }
)
_EVALUATION_FILENAMES: frozenset[str] = frozenset(
    {
        "benchmark.json",
        "report.json",
        "fail_report.json",
        "pass_report.json",
        "artifact.json",
    }
)
_EVALUATION_MARKERS: frozenset[str] = frozenset(
    {
        "benchmark",
        "evaluation",
        "comparison",
        "distillation",
        "shadow",
        "upgrade_cycle",
    }
)
_OPERATIONAL_REPORT_MARKERS: frozenset[str] = frozenset(
    {
        "readiness",
        "gate",
        "remediation",
        "decision_pack",
        "provider_health",
        "drift",
        "artifact_inventory",
        "artifact_rotation",
        "artifact_retention",
        "cleanup_eligibility",
        "protected_artifact",
        "review_required",
        "escalation",
        "blocking_summary",
        "operator_action",
        "runbook",
        "route_profile",
        "distribution",
    }
)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArtifactEntry:
    """Single artifact file entry in an inventory."""

    name: str
    path: str  # relative to artifacts_dir
    size_bytes: int
    modified_at: str  # ISO-8601 UTC
    age_days: float
    status: str  # ARTIFACT_STATUS_* constant


@dataclass(frozen=True)
class ArtifactInventoryReport:
    """Read-only snapshot of the artifacts directory state."""

    generated_at: str  # ISO-8601 UTC
    artifacts_dir: str
    stale_after_days: float
    entries: tuple[ArtifactEntry, ...]
    stale_count: int
    current_count: int
    total_size_bytes: int
    execution_enabled: bool = False  # I-150: always False

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "report_type": "artifact_inventory",
            "generated_at": self.generated_at,
            "artifacts_dir": self.artifacts_dir,
            "stale_after_days": self.stale_after_days,
            "entry_count": len(self.entries),
            "stale_count": self.stale_count,
            "current_count": self.current_count,
            "total_size_bytes": self.total_size_bytes,
            "execution_enabled": self.execution_enabled,
            "entries": [
                {
                    "name": e.name,
                    "path": e.path,
                    "size_bytes": e.size_bytes,
                    "modified_at": e.modified_at,
                    "age_days": round(e.age_days, 2),
                    "status": e.status,
                }
                for e in self.entries
            ],
        }


@dataclass(frozen=True)
class ArtifactRotationSummary:
    """Result of a rotate_stale_artifacts() call."""

    generated_at: str  # ISO-8601 UTC
    artifacts_dir: str
    archive_dir: str  # absolute path to the archive subdirectory used
    stale_after_days: float
    dry_run: bool
    archived_count: int
    skipped_count: int
    archived_paths: tuple[str, ...]  # relative paths of archived files

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "report_type": "artifact_rotation_summary",
            "generated_at": self.generated_at,
            "artifacts_dir": self.artifacts_dir,
            "archive_dir": self.archive_dir,
            "stale_after_days": self.stale_after_days,
            "dry_run": self.dry_run,
            "archived_count": self.archived_count,
            "skipped_count": self.skipped_count,
            "archived_paths": list(self.archived_paths),
        }


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


def _file_age_days(path: Path, now: datetime) -> float:
    """Return file age in fractional days based on mtime (I-151)."""
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    delta = now - mtime
    return delta.total_seconds() / 86400.0


def build_artifact_inventory(
    artifacts_dir: str | Path,
    stale_after_days: float = 30.0,
) -> ArtifactInventoryReport:
    """Scan artifacts_dir and return a read-only inventory.

    Only top-level files with managed suffixes (.json, .jsonl) are included.
    The archive/ subdirectory is excluded from the inventory.
    """
    artifacts_path = Path(artifacts_dir)
    now = datetime.now(tz=UTC)
    generated_at = now.isoformat()

    if not artifacts_path.exists():
        return ArtifactInventoryReport(
            generated_at=generated_at,
            artifacts_dir=str(artifacts_path),
            stale_after_days=stale_after_days,
            entries=(),
            stale_count=0,
            current_count=0,
            total_size_bytes=0,
        )

    entries: list[ArtifactEntry] = []
    for item in sorted(artifacts_path.iterdir()):
        # Skip directories (including the archive/ subdir)
        if item.is_dir():
            continue
        if item.suffix not in _MANAGED_SUFFIXES:
            continue

        stat = item.stat()
        age_days = _file_age_days(item, now)
        mtime_iso = datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat()
        status = ARTIFACT_STATUS_STALE if age_days > stale_after_days else ARTIFACT_STATUS_CURRENT
        entries.append(
            ArtifactEntry(
                name=item.name,
                path=str(item.relative_to(artifacts_path)),
                size_bytes=stat.st_size,
                modified_at=mtime_iso,
                age_days=age_days,
                status=status,
            )
        )

    stale = [e for e in entries if e.status == ARTIFACT_STATUS_STALE]
    current = [e for e in entries if e.status == ARTIFACT_STATUS_CURRENT]
    total_bytes = sum(e.size_bytes for e in entries)

    return ArtifactInventoryReport(
        generated_at=generated_at,
        artifacts_dir=str(artifacts_path),
        stale_after_days=stale_after_days,
        entries=tuple(entries),
        stale_count=len(stale),
        current_count=len(current),
        total_size_bytes=total_bytes,
    )


def rotate_stale_artifacts(
    artifacts_dir: str | Path,
    stale_after_days: float = 30.0,
    *,
    dry_run: bool = True,  # I-147: MUST default to True
) -> ArtifactRotationSummary:
    """Archive stale artifact files to artifacts/archive/<timestamp>/.

    When dry_run=True (default), no filesystem writes are made (I-147).
    When dry_run=False, only stale rotatable files are moved to the archive
    subdirectory. Protected or review-required artifacts are skipped.
    Files are NEVER deleted (I-148). The archive/ subdirectory itself is never
    archived (I-148).
    """
    artifacts_path = Path(artifacts_dir)
    now = datetime.now(tz=UTC)
    generated_at = now.isoformat()

    # Archive target: artifacts/archive/YYYYMMDD_HHMMSS/  (I-148)
    timestamp_slug = now.strftime("%Y%m%d_%H%M%S")
    archive_path = artifacts_path / ARCHIVE_SUBDIR / timestamp_slug

    if not artifacts_path.exists():
        return ArtifactRotationSummary(
            generated_at=generated_at,
            artifacts_dir=str(artifacts_path),
            archive_dir=str(archive_path),
            stale_after_days=stale_after_days,
            dry_run=dry_run,
            archived_count=0,
            skipped_count=0,
            archived_paths=(),
        )

    archived: list[str] = []
    skipped = 0
    retention_report = build_retention_report(
        artifacts_path,
        stale_after_days=stale_after_days,
        active_route_active=(artifacts_path / "active_route_profile.json").exists(),
    )

    for entry in retention_report.entries:
        source_path = artifacts_path / entry.path
        if not entry.rotatable:
            skipped += 1
            continue

        if not dry_run:
            archive_path.mkdir(parents=True, exist_ok=True)
            dest = archive_path / entry.path
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source_path), str(dest))  # I-148: move, never delete
        archived.append(entry.path)

    return ArtifactRotationSummary(
        generated_at=generated_at,
        artifacts_dir=str(artifacts_path),
        archive_dir=str(archive_path),
        stale_after_days=stale_after_days,
        dry_run=dry_run,
        archived_count=len(archived),
        skipped_count=skipped,
        archived_paths=tuple(archived),
    )


def save_artifact_inventory(
    report: ArtifactInventoryReport,
    path: str | Path,
) -> Path:
    """Persist an ArtifactInventoryReport as JSON."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(report.to_json_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return out


def save_artifact_rotation_summary(
    summary: ArtifactRotationSummary,
    path: str | Path,
) -> Path:
    """Persist an ArtifactRotationSummary as JSON."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(summary.to_json_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return out


# ---------------------------------------------------------------------------
# Sprint 25: Retention policy data models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArtifactRetentionEntry:
    """Single artifact entry with retention classification (Sprint 25).

    Invariants:
    - delete_eligible is always False (I-154) — deletion is never platform-initiated.
    - protected=True entries must not appear as rotation candidates (I-155).
    """

    name: str
    path: str  # relative to artifacts_dir
    size_bytes: int
    modified_at: str  # ISO-8601 UTC
    age_days: float
    status: str  # ARTIFACT_STATUS_* constant
    artifact_class: str  # ARTIFACT_CLASS_* constant
    retention_class: str  # RETENTION_CLASS_* constant
    protected: bool  # True → never archive (I-155)
    rotatable: bool  # True → stale and safe to archive
    delete_eligible: bool = False  # always False (I-154)
    retention_rationale: str = ""  # why the artifact landed in this class
    operator_guidance: str = ""  # human-readable operator hint

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "size_bytes": self.size_bytes,
            "modified_at": self.modified_at,
            "age_days": round(self.age_days, 2),
            "status": self.status,
            "artifact_class": self.artifact_class,
            "retention_class": self.retention_class,
            "protected": self.protected,
            "rotatable": self.rotatable,
            "delete_eligible": self.delete_eligible,
            "retention_rationale": self.retention_rationale,
            "operator_guidance": self.operator_guidance,
        }


@dataclass(frozen=True)
class ArtifactRetentionReport:
    """Read-only retention policy classification for the artifacts directory (Sprint 25).

    Invariants (I-161):
    - execution_enabled is always False.
    - write_back_allowed is always False.
    - delete_eligible_count is always 0.
    """

    generated_at: str
    artifacts_dir: str
    stale_after_days: float
    entries: tuple[ArtifactRetentionEntry, ...]
    total_count: int
    protected_count: int
    rotatable_count: int
    review_required_count: int
    execution_enabled: bool = False  # always False (I-161)
    write_back_allowed: bool = False  # always False (I-161)
    delete_eligible_count: int = 0  # always 0 (I-154)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "report_type": "artifact_retention_report",
            "generated_at": self.generated_at,
            "artifacts_dir": self.artifacts_dir,
            "stale_after_days": self.stale_after_days,
            "total_count": self.total_count,
            "protected_count": self.protected_count,
            "rotatable_count": self.rotatable_count,
            "review_required_count": self.review_required_count,
            "execution_enabled": self.execution_enabled,
            "write_back_allowed": self.write_back_allowed,
            "delete_eligible_count": self.delete_eligible_count,
            "entries": [e.to_json_dict() for e in self.entries],
        }


@dataclass(frozen=True)
class ArtifactCleanupEligibilitySummary:
    """Read-only cleanup eligibility view derived from the retention report only."""

    generated_at: str
    artifacts_dir: str
    stale_after_days: float
    cleanup_eligible_count: int
    protected_count: int
    review_required_count: int
    candidates: tuple[ArtifactRetentionEntry, ...]
    dry_run_default: bool = True
    delete_eligible_count: int = 0
    execution_enabled: bool = False
    write_back_allowed: bool = False

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "report_type": "cleanup_eligibility_summary",
            "generated_at": self.generated_at,
            "artifacts_dir": self.artifacts_dir,
            "stale_after_days": self.stale_after_days,
            "cleanup_eligible_count": self.cleanup_eligible_count,
            "protected_count": self.protected_count,
            "review_required_count": self.review_required_count,
            "dry_run_default": self.dry_run_default,
            "delete_eligible_count": self.delete_eligible_count,
            "execution_enabled": self.execution_enabled,
            "write_back_allowed": self.write_back_allowed,
            "candidates": [entry.to_json_dict() for entry in self.candidates],
        }


@dataclass(frozen=True)
class ProtectedArtifactSummary:
    """Read-only summary of artifacts that stay protected under the retention policy."""

    generated_at: str
    artifacts_dir: str
    protected_count: int
    entries: tuple[ArtifactRetentionEntry, ...]
    execution_enabled: bool = False
    write_back_allowed: bool = False
    delete_eligible_count: int = 0

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "report_type": "protected_artifact_summary",
            "generated_at": self.generated_at,
            "artifacts_dir": self.artifacts_dir,
            "protected_count": self.protected_count,
            "execution_enabled": self.execution_enabled,
            "write_back_allowed": self.write_back_allowed,
            "delete_eligible_count": self.delete_eligible_count,
            "entries": [entry.to_json_dict() for entry in self.entries],
        }


@dataclass(frozen=True)
class ReviewRequiredArtifactSummary:
    """Read-only summary of artifacts requiring operator review prior to archival (Sprint 26)."""

    generated_at: str
    artifacts_dir: str
    review_required_count: int
    entries: tuple[ArtifactRetentionEntry, ...]
    execution_enabled: bool = False
    write_back_allowed: bool = False
    delete_eligible_count: int = 0

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "report_type": "review_required_artifact_summary",
            "generated_at": self.generated_at,
            "artifacts_dir": self.artifacts_dir,
            "review_required_count": self.review_required_count,
            "execution_enabled": self.execution_enabled,
            "write_back_allowed": self.write_back_allowed,
            "delete_eligible_count": self.delete_eligible_count,
            "entries": [entry.to_json_dict() for entry in self.entries],
        }


# ---------------------------------------------------------------------------
# Sprint 25: Retention classification functions
# ---------------------------------------------------------------------------


def _base_fields(entry: ArtifactEntry) -> dict[str, object]:
    """Extract ArtifactEntry fields shared with ArtifactRetentionEntry."""
    return {
        "name": entry.name,
        "path": entry.path,
        "size_bytes": entry.size_bytes,
        "modified_at": entry.modified_at,
        "age_days": entry.age_days,
        "status": entry.status,
    }


def _protected_entry(
    entry: ArtifactEntry,
    *,
    artifact_class: str,
    rationale: str,
    guidance: str,
) -> ArtifactRetentionEntry:
    """Return a protected (never-archive) retention entry."""
    return ArtifactRetentionEntry(
        **_base_fields(entry),  # type: ignore[arg-type]
        artifact_class=artifact_class,
        retention_class=RETENTION_CLASS_PROTECTED,
        protected=True,
        rotatable=False,
        delete_eligible=False,
        retention_rationale=rationale,
        operator_guidance=guidance,
    )


def _classified_entry(
    entry: ArtifactEntry,
    *,
    artifact_class: str,
    retention_class: str,
    protected: bool,
    rotatable: bool,
    rationale: str,
    guidance: str,
) -> ArtifactRetentionEntry:
    """Return a retention entry with explicit rationale/guidance."""
    return ArtifactRetentionEntry(
        **_base_fields(entry),  # type: ignore[arg-type]
        artifact_class=artifact_class,
        retention_class=retention_class,
        protected=protected,
        rotatable=rotatable,
        delete_eligible=False,
        retention_rationale=rationale,
        operator_guidance=guidance,
    )


def _is_stale(entry: ArtifactEntry) -> bool:
    return entry.status == ARTIFACT_STATUS_STALE


def _matches_report_markers(
    entry: ArtifactEntry,
    *,
    markers: frozenset[str],
) -> bool:
    lowered = f"{entry.path}:{entry.name}".lower()
    return any(marker in lowered for marker in markers)


def classify_artifact_retention(
    entry: ArtifactEntry,
    *,
    active_route_active: bool = False,
) -> ArtifactRetentionEntry:
    """Classify a single ArtifactEntry by retention policy (I-153–I-159).

    Pure classification — no filesystem reads, no writes (I-160).
    delete_eligible is always False (I-154).
    """
    name = entry.name

    lowered_name = name.lower()

    if (
        name in _PROTECTED_AUDIT_FILENAMES
        or lowered_name
        in {
            "handoff.json",
            "handoffs.jsonl",
        }
        or any(marker in lowered_name for marker in _PROTECTED_AUDIT_NAME_MARKERS)
    ):
        # I-156: audit trails are always protected
        return _protected_entry(
            entry,
            artifact_class=ARTIFACT_CLASS_AUDIT_TRAIL,
            rationale="Append-only audit artifacts must remain intact for traceability.",
            guidance="Audit trail — never archive (I-156)",
        )

    if name in _PROTECTED_PROMOTION_FILENAMES:
        # I-157: promotion records are always protected
        return _protected_entry(
            entry,
            artifact_class=ARTIFACT_CLASS_PROMOTION,
            rationale="Promotion records are immutable audit evidence for operator decisions.",
            guidance="Promotion record — never archive (I-157)",
        )

    if name in _PROTECTED_TRAINING_FILENAMES:
        # I-158: training data is always protected
        return _protected_entry(
            entry,
            artifact_class=ARTIFACT_CLASS_TRAINING_DATA,
            rationale="Training artifacts are retained for reproducibility and audit linkage.",
            guidance="Training data — never archive (I-158)",
        )

    if name in _ACTIVE_STATE_FILENAMES:
        # I-159: active route state is protected while the route is active
        if active_route_active:
            return _protected_entry(
                entry,
                artifact_class=ARTIFACT_CLASS_ACTIVE_STATE,
                rationale="The active route state defines live guarded runtime behavior.",
                guidance="Active routing state — protected while route is active (I-159)",
            )
        is_stale = _is_stale(entry)
        ret_class = RETENTION_CLASS_ROTATABLE if is_stale else RETENTION_CLASS_REVIEW_REQUIRED
        guidance = (
            "Inactive route state — rotatable when stale; confirm route is inactive"
            if is_stale
            else "Inactive route state — not yet stale; confirm route is inactive first"
        )
        return _classified_entry(
            entry,
            artifact_class=ARTIFACT_CLASS_ACTIVE_STATE,
            retention_class=ret_class,
            protected=False,
            rotatable=is_stale,
            rationale=(
                "Inactive route state can be archived after it becomes stale."
                if is_stale
                else "Inactive route state still needs short-term operator visibility."
            ),
            guidance=guidance,
        )

    if name in _EVALUATION_FILENAMES or _matches_report_markers(entry, markers=_EVALUATION_MARKERS):
        is_stale = _is_stale(entry)
        ret_class = RETENTION_CLASS_ROTATABLE if is_stale else RETENTION_CLASS_REVIEW_REQUIRED
        return _classified_entry(
            entry,
            artifact_class=ARTIFACT_CLASS_EVALUATION,
            retention_class=ret_class,
            protected=False,
            rotatable=is_stale,
            rationale=(
                "Derived evaluation/report artifacts can be archived once stale."
                if is_stale
                else "Derived evaluation/report artifacts remain visible until stale."
            ),
            guidance=(
                "Evaluation artifact — rotatable when stale"
                if is_stale
                else "Evaluation artifact — not yet stale"
            ),
        )

    if _matches_report_markers(entry, markers=_OPERATIONAL_REPORT_MARKERS):
        is_stale = _is_stale(entry)
        ret_class = RETENTION_CLASS_ROTATABLE if is_stale else RETENTION_CLASS_REVIEW_REQUIRED
        return _classified_entry(
            entry,
            artifact_class=ARTIFACT_CLASS_OPERATIONAL,
            retention_class=ret_class,
            protected=False,
            rotatable=is_stale,
            rationale=(
                "Derived operational reports are archive-safe once they become stale."
                if is_stale
                else "Operational reports remain review-visible until stale."
            ),
            guidance=(
                "Operational report — rotatable when stale"
                if is_stale
                else "Operational report — not yet stale"
            ),
        )

    # Unknown artifact — operator review required
    return _classified_entry(
        entry,
        artifact_class=ARTIFACT_CLASS_UNKNOWN,
        retention_class=RETENTION_CLASS_REVIEW_REQUIRED,
        protected=False,
        rotatable=False,
        rationale="Unknown artifacts fail closed and require manual classification first.",
        guidance="Unknown artifact class — operator review required before any archival",
    )


def build_retention_report(
    artifacts_dir: str | Path,
    stale_after_days: float = 30.0,
    *,
    active_route_active: bool = False,
) -> ArtifactRetentionReport:
    """Build a read-only retention classification for all artifacts in artifacts_dir.

    Pure computation — no DB reads, no LLM calls, no network, no filesystem writes (I-160).
    delete_eligible_count is always 0 (I-154).
    """
    inventory = build_artifact_inventory(artifacts_dir, stale_after_days)
    entries = tuple(
        classify_artifact_retention(e, active_route_active=active_route_active)
        for e in inventory.entries
    )

    protected = [e for e in entries if e.retention_class == RETENTION_CLASS_PROTECTED]
    rotatable = [e for e in entries if e.retention_class == RETENTION_CLASS_ROTATABLE]
    review = [e for e in entries if e.retention_class == RETENTION_CLASS_REVIEW_REQUIRED]

    return ArtifactRetentionReport(
        generated_at=inventory.generated_at,
        artifacts_dir=inventory.artifacts_dir,
        stale_after_days=stale_after_days,
        entries=entries,
        total_count=len(entries),
        protected_count=len(protected),
        rotatable_count=len(rotatable),
        review_required_count=len(review),
        execution_enabled=False,
        write_back_allowed=False,
        delete_eligible_count=0,
    )


def build_cleanup_eligibility_summary(
    report: ArtifactRetentionReport,
) -> ArtifactCleanupEligibilitySummary:
    """Project cleanup/archive eligibility from the canonical retention report only."""
    candidates = tuple(entry for entry in report.entries if entry.rotatable)
    return ArtifactCleanupEligibilitySummary(
        generated_at=report.generated_at,
        artifacts_dir=report.artifacts_dir,
        stale_after_days=report.stale_after_days,
        cleanup_eligible_count=len(candidates),
        protected_count=report.protected_count,
        review_required_count=report.review_required_count,
        candidates=candidates,
        dry_run_default=True,
        delete_eligible_count=0,
        execution_enabled=False,
        write_back_allowed=False,
    )


def build_protected_artifact_summary(
    report: ArtifactRetentionReport,
) -> ProtectedArtifactSummary:
    """Project protected artifacts from the canonical retention report only."""
    entries = tuple(entry for entry in report.entries if entry.protected)
    return ProtectedArtifactSummary(
        generated_at=report.generated_at,
        artifacts_dir=report.artifacts_dir,
        protected_count=len(entries),
        entries=entries,
        execution_enabled=False,
        write_back_allowed=False,
        delete_eligible_count=0,
    )


def build_review_required_summary(
    report: ArtifactRetentionReport,
) -> ReviewRequiredArtifactSummary:
    """Project review-required artifacts from the canonical retention report only."""
    entries = tuple(
        entry
        for entry in report.entries
        if entry.retention_class == RETENTION_CLASS_REVIEW_REQUIRED
    )
    return ReviewRequiredArtifactSummary(
        generated_at=report.generated_at,
        artifacts_dir=report.artifacts_dir,
        review_required_count=len(entries),
        entries=entries,
        execution_enabled=False,
        write_back_allowed=False,
        delete_eligible_count=0,
    )


def save_retention_report(
    report: ArtifactRetentionReport,
    path: str | Path,
) -> Path:
    """Persist an ArtifactRetentionReport as JSON."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(report.to_json_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return out


def save_cleanup_eligibility_summary(
    summary: ArtifactCleanupEligibilitySummary,
    path: str | Path,
) -> Path:
    """Persist an ArtifactCleanupEligibilitySummary as JSON."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(summary.to_json_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return out


def save_protected_artifact_summary(
    summary: ProtectedArtifactSummary,
    path: str | Path,
) -> Path:
    """Persist a ProtectedArtifactSummary as JSON."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(summary.to_json_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return out


def save_review_required_summary(
    summary: ReviewRequiredArtifactSummary,
    path: str | Path,
) -> Path:
    """Persist a ReviewRequiredArtifactSummary as JSON."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(summary.to_json_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return out
