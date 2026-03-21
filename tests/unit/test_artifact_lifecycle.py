"""Unit tests for Artifact Lifecycle Management Surface (Sprint 24 + Sprint 25).

Covers:
- build_artifact_inventory(): nominal, missing dir, stale detection
- rotate_stale_artifacts(): dry-run, real rotation, skips current/dirs
- save_artifact_inventory() / save_artifact_rotation_summary(): JSON persistence
- Invariant checks (execution_enabled=False, archive stays inside artifacts, etc.)
- Sprint 25: classify_artifact_retention(), build_retention_report(), save_retention_report()
"""

from __future__ import annotations

import json
from pathlib import Path

from app.research.artifact_lifecycle import (
    ARCHIVE_SUBDIR,
    ARTIFACT_CLASS_ACTIVE_STATE,
    ARTIFACT_CLASS_AUDIT_TRAIL,
    ARTIFACT_CLASS_EVALUATION,
    ARTIFACT_CLASS_OPERATIONAL,
    ARTIFACT_CLASS_PROMOTION,
    ARTIFACT_CLASS_TRAINING_DATA,
    ARTIFACT_CLASS_UNKNOWN,
    ARTIFACT_STATUS_CURRENT,
    ARTIFACT_STATUS_STALE,
    RETENTION_CLASS_PROTECTED,
    RETENTION_CLASS_REVIEW_REQUIRED,
    RETENTION_CLASS_ROTATABLE,
    ArtifactEntry,
    ArtifactInventoryReport,
    build_artifact_inventory,
    build_cleanup_eligibility_summary,
    build_protected_artifact_summary,
    build_retention_report,
    build_review_required_summary,
    classify_artifact_retention,
    rotate_stale_artifacts,
    save_artifact_inventory,
    save_artifact_rotation_summary,
    save_cleanup_eligibility_summary,
    save_protected_artifact_summary,
    save_retention_report,
    save_review_required_summary,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_artifact(tmp_path: Path, name: str, content: str = "{}") -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def _age_file(path: Path, days: float) -> None:
    """Back-date a file's mtime by `days`."""
    import os

    new_mtime = path.stat().st_mtime - days * 86400
    os.utime(path, (new_mtime, new_mtime))


# ---------------------------------------------------------------------------
# build_artifact_inventory
# ---------------------------------------------------------------------------


def test_inventory_missing_dir_returns_empty(tmp_path: Path) -> None:
    missing = tmp_path / "no_such_artifacts"
    report = build_artifact_inventory(missing)
    assert isinstance(report, ArtifactInventoryReport)
    assert report.entries == ()
    assert report.stale_count == 0
    assert report.current_count == 0
    assert report.total_size_bytes == 0


def test_inventory_execution_enabled_always_false(tmp_path: Path) -> None:
    """I-150: execution_enabled MUST always be False."""
    report = build_artifact_inventory(tmp_path)
    assert report.execution_enabled is False


def test_inventory_empty_dir_returns_zero_counts(tmp_path: Path) -> None:
    report = build_artifact_inventory(tmp_path)
    assert len(report.entries) == 0
    assert report.stale_count == 0
    assert report.current_count == 0


def test_inventory_counts_json_and_jsonl_files(tmp_path: Path) -> None:
    _make_artifact(tmp_path, "report.json")
    _make_artifact(tmp_path, "candidate.jsonl")
    _make_artifact(tmp_path, "ignore.txt")  # not managed
    report = build_artifact_inventory(tmp_path)
    assert len(report.entries) == 2
    names = {e.name for e in report.entries}
    assert "report.json" in names
    assert "candidate.jsonl" in names
    assert "ignore.txt" not in names


def test_inventory_detects_stale_file(tmp_path: Path) -> None:
    p = _make_artifact(tmp_path, "old.json")
    _age_file(p, 35)  # 35 days old, threshold is 30
    report = build_artifact_inventory(tmp_path, stale_after_days=30.0)
    assert report.stale_count == 1
    assert report.current_count == 0
    entry = report.entries[0]
    assert entry.status == ARTIFACT_STATUS_STALE
    assert entry.age_days > 30


def test_inventory_marks_fresh_file_as_current(tmp_path: Path) -> None:
    _make_artifact(tmp_path, "fresh.json")
    report = build_artifact_inventory(tmp_path, stale_after_days=30.0)
    assert report.current_count == 1
    assert report.stale_count == 0
    assert report.entries[0].status == ARTIFACT_STATUS_CURRENT


def test_inventory_excludes_archive_subdirectory(tmp_path: Path) -> None:
    archive = tmp_path / ARCHIVE_SUBDIR
    archive.mkdir()
    (archive / "old.json").write_text("{}", encoding="utf-8")
    _make_artifact(tmp_path, "current.json")
    report = build_artifact_inventory(tmp_path)
    # Only the top-level file; archive dir contents are excluded
    assert len(report.entries) == 1
    assert report.entries[0].name == "current.json"


def test_inventory_total_size_bytes_sum(tmp_path: Path) -> None:
    p1 = _make_artifact(tmp_path, "a.json", '{"x": 1}')
    p2 = _make_artifact(tmp_path, "b.jsonl", '{"y": 2}')
    expected = p1.stat().st_size + p2.stat().st_size
    report = build_artifact_inventory(tmp_path)
    assert report.total_size_bytes == expected


def test_inventory_entry_fields_populated(tmp_path: Path) -> None:
    _make_artifact(tmp_path, "entry.json", '{"k": "v"}')
    report = build_artifact_inventory(tmp_path)
    entry = report.entries[0]
    assert isinstance(entry, ArtifactEntry)
    assert entry.name == "entry.json"
    assert entry.path == "entry.json"
    assert entry.size_bytes > 0
    assert entry.modified_at  # ISO string
    assert entry.age_days >= 0


# ---------------------------------------------------------------------------
# rotate_stale_artifacts — dry-run mode
# ---------------------------------------------------------------------------


def test_rotate_dry_run_default_makes_no_changes(tmp_path: Path) -> None:
    """I-147: dry_run=True is the default. No filesystem writes."""
    p = _make_artifact(tmp_path, "benchmark.json")
    _age_file(p, 40)
    summary = rotate_stale_artifacts(tmp_path, stale_after_days=30.0)
    assert summary.dry_run is True
    assert summary.archived_count == 1
    # File still present (dry run — no actual move)
    assert p.exists()
    # No archive dir created
    assert not (tmp_path / ARCHIVE_SUBDIR).exists()


def test_rotate_dry_run_reports_stale_paths(tmp_path: Path) -> None:
    p = _make_artifact(tmp_path, "report.json")
    _age_file(p, 60)
    summary = rotate_stale_artifacts(tmp_path, stale_after_days=30.0, dry_run=True)
    assert "report.json" in summary.archived_paths


def test_rotate_dry_run_skips_current_files(tmp_path: Path) -> None:
    _make_artifact(tmp_path, "fresh.json")
    summary = rotate_stale_artifacts(tmp_path, stale_after_days=30.0, dry_run=True)
    assert summary.archived_count == 0
    assert summary.skipped_count == 1


def test_rotate_missing_dir_returns_empty_summary(tmp_path: Path) -> None:
    missing = tmp_path / "no_artifacts"
    summary = rotate_stale_artifacts(missing)
    assert summary.archived_count == 0
    assert summary.skipped_count == 0
    assert summary.archived_paths == ()


# ---------------------------------------------------------------------------
# rotate_stale_artifacts — real (no-dry-run) mode
# ---------------------------------------------------------------------------


def test_rotate_no_dry_run_moves_stale_file(tmp_path: Path) -> None:
    """I-148: stale files are moved to archive/<timestamp>/, never deleted."""
    p = _make_artifact(tmp_path, "benchmark.json")
    _age_file(p, 45)
    summary = rotate_stale_artifacts(tmp_path, stale_after_days=30.0, dry_run=False)
    assert summary.archived_count == 1
    # Original file is gone (moved, not deleted)
    assert not p.exists()
    # File is in the archive subdirectory
    archive_root = tmp_path / ARCHIVE_SUBDIR
    assert archive_root.exists()
    archived_files = list(archive_root.rglob("benchmark.json"))
    assert len(archived_files) == 1


def test_rotate_no_dry_run_archive_inside_artifacts_dir(tmp_path: Path) -> None:
    """I-148: archive must be a subdir of the managed artifacts directory.

    Uses benchmark.json (EVALUATION class → rotatable when stale) so that
    an actual archive operation is triggered and the path can be validated.
    """
    p = _make_artifact(tmp_path, "benchmark.json")
    _age_file(p, 35)
    summary = rotate_stale_artifacts(tmp_path, stale_after_days=30.0, dry_run=False)
    # An actual archive happened
    assert summary.archived_count == 1
    archive_dir = Path(summary.archive_dir)
    assert archive_dir.is_relative_to(tmp_path)
    assert ARCHIVE_SUBDIR in archive_dir.parts
    # Archive directory was created on disk
    assert archive_dir.exists()


def test_rotate_no_dry_run_leaves_current_files(tmp_path: Path) -> None:
    fresh = _make_artifact(tmp_path, "fresh.json")
    stale = _make_artifact(tmp_path, "benchmark.json")
    _age_file(stale, 40)
    rotate_stale_artifacts(tmp_path, stale_after_days=30.0, dry_run=False)
    assert fresh.exists()
    assert not stale.exists()


def test_rotate_skips_directories_always(tmp_path: Path) -> None:
    (tmp_path / "subdir").mkdir()
    summary = rotate_stale_artifacts(tmp_path, stale_after_days=0.0, dry_run=False)
    # The directory itself is never archived
    assert (tmp_path / "subdir").exists()
    assert summary.archived_count == 0


def test_rotate_skips_unmanaged_file_extensions(tmp_path: Path) -> None:
    p = tmp_path / "notes.txt"
    p.write_text("notes", encoding="utf-8")
    _age_file(p, 100)
    summary = rotate_stale_artifacts(tmp_path, stale_after_days=0.0, dry_run=False)
    assert p.exists()  # not archived
    assert summary.archived_count == 0


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_save_artifact_inventory_writes_json(tmp_path: Path) -> None:
    arts = tmp_path / "arts"
    arts.mkdir()
    _make_artifact(arts, "data.json")
    report = build_artifact_inventory(arts)
    out = tmp_path / "output" / "inventory.json"
    saved = save_artifact_inventory(report, out)
    assert saved == out
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["report_type"] == "artifact_inventory"
    assert "generated_at" in data
    assert data["execution_enabled"] is False


def test_save_rotation_summary_writes_json(tmp_path: Path) -> None:
    summary = rotate_stale_artifacts(tmp_path, dry_run=True)
    out = tmp_path / "rotation.json"
    saved = save_artifact_rotation_summary(summary, out)
    assert saved == out
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["report_type"] == "artifact_rotation_summary"
    assert data["dry_run"] is True


def test_inventory_to_json_dict_serializable(tmp_path: Path) -> None:
    _make_artifact(tmp_path, "x.json")
    report = build_artifact_inventory(tmp_path)
    d = report.to_json_dict()
    # Must be fully JSON-serializable
    dumped = json.dumps(d)
    loaded = json.loads(dumped)
    assert loaded["report_type"] == "artifact_inventory"
    assert isinstance(loaded["entries"], list)


# ---------------------------------------------------------------------------
# Sprint 25: Retention policy tests
# ---------------------------------------------------------------------------


def _make_current_entry(name: str) -> ArtifactEntry:
    return ArtifactEntry(
        name=name,
        path=name,
        size_bytes=100,
        modified_at="2026-03-20T00:00:00+00:00",
        age_days=1.0,
        status=ARTIFACT_STATUS_CURRENT,
    )


def _make_stale_entry(name: str) -> ArtifactEntry:
    return ArtifactEntry(
        name=name,
        path=name,
        size_bytes=100,
        modified_at="2026-01-01T00:00:00+00:00",
        age_days=79.0,
        status=ARTIFACT_STATUS_STALE,
    )


def test_classify_audit_trail_always_protected() -> None:
    for name in (
        "mcp_write_audit.jsonl",
        "consumer_acknowledgements.jsonl",
        "alert_audit.jsonl",
        "operator_review_journal.jsonl",
    ):
        entry = _make_stale_entry(name)
        result = classify_artifact_retention(entry)
        assert result.artifact_class == ARTIFACT_CLASS_AUDIT_TRAIL, name
        assert result.retention_class == RETENTION_CLASS_PROTECTED, name
        assert result.protected is True, name
        assert result.rotatable is False, name
        assert result.delete_eligible is False, name  # I-154


def test_classify_signal_handoff_artifacts_as_protected_audit_trail() -> None:
    for name in ("handoffs.jsonl", "handoff.json", "execution_signal_handoff.json"):
        entry = _make_current_entry(name)
        result = classify_artifact_retention(entry)
        assert result.artifact_class == ARTIFACT_CLASS_AUDIT_TRAIL, name
        assert result.retention_class == RETENTION_CLASS_PROTECTED, name
        assert result.protected is True, name
        assert result.rotatable is False, name
        assert result.delete_eligible is False, name


def test_classify_promotion_always_protected() -> None:
    entry = _make_stale_entry("promotion_record.json")
    result = classify_artifact_retention(entry)
    assert result.artifact_class == ARTIFACT_CLASS_PROMOTION
    assert result.retention_class == RETENTION_CLASS_PROTECTED
    assert result.protected is True
    assert result.rotatable is False
    assert result.delete_eligible is False  # I-154


def test_classify_training_data_always_protected() -> None:
    for name in ("teacher.jsonl", "candidate.jsonl", "tuning_manifest.json"):
        entry = _make_stale_entry(name)
        result = classify_artifact_retention(entry)
        assert result.artifact_class == ARTIFACT_CLASS_TRAINING_DATA, name
        assert result.retention_class == RETENTION_CLASS_PROTECTED, name
        assert result.protected is True, name
        assert result.rotatable is False, name
        assert result.delete_eligible is False, name  # I-154


def test_classify_active_state_protected_when_route_active() -> None:
    entry = _make_stale_entry("active_route_profile.json")
    result = classify_artifact_retention(entry, active_route_active=True)
    assert result.artifact_class == ARTIFACT_CLASS_ACTIVE_STATE
    assert result.retention_class == RETENTION_CLASS_PROTECTED  # I-159
    assert result.protected is True
    assert result.rotatable is False
    assert result.delete_eligible is False  # I-154


def test_classify_active_state_rotatable_when_inactive_and_stale() -> None:
    entry = _make_stale_entry("active_route_profile.json")
    result = classify_artifact_retention(entry, active_route_active=False)
    assert result.artifact_class == ARTIFACT_CLASS_ACTIVE_STATE
    assert result.retention_class == RETENTION_CLASS_ROTATABLE
    assert result.protected is False
    assert result.rotatable is True
    assert result.delete_eligible is False  # I-154


def test_classify_active_state_review_required_when_inactive_not_stale() -> None:
    entry = _make_current_entry("active_route_profile.json")
    result = classify_artifact_retention(entry, active_route_active=False)
    assert result.retention_class == RETENTION_CLASS_REVIEW_REQUIRED
    assert result.protected is False
    assert result.rotatable is False
    assert result.delete_eligible is False  # I-154


def test_classify_evaluation_rotatable_when_stale() -> None:
    for name in ("benchmark.json", "report.json", "fail_report.json"):
        entry = _make_stale_entry(name)
        result = classify_artifact_retention(entry)
        assert result.artifact_class == ARTIFACT_CLASS_EVALUATION, name
        assert result.retention_class == RETENTION_CLASS_ROTATABLE, name
        assert result.rotatable is True, name
        assert result.delete_eligible is False, name  # I-154


def test_classify_evaluation_review_required_when_not_stale() -> None:
    entry = _make_current_entry("benchmark.json")
    result = classify_artifact_retention(entry)
    assert result.artifact_class == ARTIFACT_CLASS_EVALUATION
    assert result.retention_class == RETENTION_CLASS_REVIEW_REQUIRED
    assert result.rotatable is False
    assert result.delete_eligible is False  # I-154


def test_classify_operational_artifact_rotatable_when_stale() -> None:
    """OPERATIONAL class (I-164): readiness/gate/drift/etc. reports rotatable when stale."""
    for name in (
        "readiness_report.json",
        "gate_summary.json",
        "remediation_report.json",
        "provider_health_summary.json",
        "drift_summary.json",
        "artifact_inventory_report.json",
        "artifact_rotation_summary.json",
        "artifact_retention_report.json",
        "cleanup_eligibility_summary.json",
        "protected_artifact_summary.json",
        "escalation_summary.json",
        "blocking_summary.json",
        "operator_action_summary.json",
        "route_profile_report.json",
        "distribution_report.json",
    ):
        entry = _make_stale_entry(name)
        result = classify_artifact_retention(entry)
        assert result.artifact_class == ARTIFACT_CLASS_OPERATIONAL, name
        assert result.retention_class == RETENTION_CLASS_ROTATABLE, name
        assert result.protected is False, name
        assert result.rotatable is True, name
        assert result.delete_eligible is False, name  # I-154


def test_classify_operational_artifact_review_required_when_current() -> None:
    """OPERATIONAL class: non-stale operational reports must not be rotated."""
    entry = _make_current_entry("escalation_summary.json")
    result = classify_artifact_retention(entry)
    assert result.artifact_class == ARTIFACT_CLASS_OPERATIONAL
    assert result.retention_class == RETENTION_CLASS_REVIEW_REQUIRED
    assert result.rotatable is False
    assert result.delete_eligible is False  # I-154


def test_classify_unknown_artifact_review_required() -> None:
    entry = _make_stale_entry("some_unknown_file.json")
    result = classify_artifact_retention(entry)
    assert result.artifact_class == ARTIFACT_CLASS_UNKNOWN
    assert result.retention_class == RETENTION_CLASS_REVIEW_REQUIRED
    assert result.protected is False
    assert result.rotatable is False
    assert result.delete_eligible is False  # I-154


def test_build_retention_report_counts(tmp_path: Path) -> None:
    """Protected + rotatable + review_required counts must sum to total."""
    _make_artifact(tmp_path, "mcp_write_audit.jsonl")  # audit → protected
    _make_artifact(tmp_path, "promotion_record.json")  # promotion → protected
    _make_artifact(tmp_path, "unknown_thing.json")  # unknown → review_required

    report = build_retention_report(tmp_path, stale_after_days=30.0)
    assert report.total_count == 3
    # all current → protected=2, review_required=1 (unknown), rotatable=0
    assert report.protected_count == 2
    assert report.review_required_count == 1
    assert report.rotatable_count == 0
    assert report.delete_eligible_count == 0  # always 0 (I-154)
    assert report.execution_enabled is False  # I-161
    assert report.write_back_allowed is False  # I-161


def test_build_retention_report_stale_rotatable(tmp_path: Path) -> None:
    """Stale evaluation artifact becomes rotatable."""
    p = _make_artifact(tmp_path, "benchmark.json")
    _age_file(p, 35.0)  # stale at 30-day threshold

    report = build_retention_report(tmp_path, stale_after_days=30.0)
    assert report.rotatable_count == 1
    assert report.protected_count == 0


def test_build_retention_report_empty_dir(tmp_path: Path) -> None:
    report = build_retention_report(tmp_path)
    assert report.total_count == 0
    assert report.protected_count == 0
    assert report.rotatable_count == 0
    assert report.review_required_count == 0
    assert report.delete_eligible_count == 0  # I-154


def test_build_retention_report_missing_dir(tmp_path: Path) -> None:
    missing = tmp_path / "no_such_dir"
    report = build_retention_report(missing)
    assert report.total_count == 0


def test_save_retention_report_creates_json(tmp_path: Path) -> None:
    _make_artifact(tmp_path, "mcp_write_audit.jsonl")
    report = build_retention_report(tmp_path)
    out = tmp_path / "out" / "retention.json"
    result = save_retention_report(report, out)
    assert result == out
    assert out.exists()
    data = json.loads(out.read_text())
    assert data["report_type"] == "artifact_retention_report"
    assert data["delete_eligible_count"] == 0  # I-154
    assert data["execution_enabled"] is False  # I-161
    assert data["write_back_allowed"] is False  # I-161


def test_retention_report_serializable(tmp_path: Path) -> None:
    _make_artifact(tmp_path, "teacher.jsonl")
    _make_artifact(tmp_path, "benchmark.json")
    report = build_retention_report(tmp_path)
    dumped = json.dumps(report.to_json_dict())
    loaded = json.loads(dumped)
    assert loaded["report_type"] == "artifact_retention_report"
    assert isinstance(loaded["entries"], list)
    entry = loaded["entries"][0]
    assert "artifact_class" in entry
    assert "retention_class" in entry
    assert "protected" in entry
    assert "rotatable" in entry
    assert entry["delete_eligible"] is False  # I-154


def test_retention_report_includes_rationale_and_guidance(tmp_path: Path) -> None:
    _make_artifact(tmp_path, "mcp_write_audit.jsonl")

    report = build_retention_report(tmp_path)

    entry = report.entries[0]
    assert entry.retention_rationale
    assert entry.operator_guidance
    payload = report.to_json_dict()["entries"][0]
    assert payload["retention_rationale"]
    assert payload["operator_guidance"]


def test_build_cleanup_eligibility_summary_lists_rotatable_only(tmp_path: Path) -> None:
    stale_report = _make_artifact(tmp_path, "readiness_report.json")
    _age_file(stale_report, 45.0)
    _make_artifact(tmp_path, "mcp_write_audit.jsonl")

    report = build_retention_report(tmp_path, stale_after_days=30.0)
    summary = build_cleanup_eligibility_summary(report)

    assert summary.cleanup_eligible_count == 1
    assert summary.protected_count == 1
    assert summary.review_required_count == 0
    assert summary.dry_run_default is True
    assert summary.delete_eligible_count == 0
    assert summary.candidates[0].path == "readiness_report.json"


def test_build_protected_artifact_summary_lists_only_protected_entries(
    tmp_path: Path,
) -> None:
    _make_artifact(tmp_path, "mcp_write_audit.jsonl")
    _make_artifact(tmp_path, "promotion_record.json")
    _make_artifact(tmp_path, "benchmark.json")

    report = build_retention_report(tmp_path, stale_after_days=30.0)
    summary = build_protected_artifact_summary(report)

    assert summary.protected_count == 2
    protected_paths = {entry.path for entry in summary.entries}
    assert protected_paths == {
        "mcp_write_audit.jsonl",
        "promotion_record.json",
    }


def test_build_review_required_summary_lists_only_review_entries(
    tmp_path: Path,
) -> None:
    _make_artifact(tmp_path, "readiness_report.json")
    _make_artifact(tmp_path, "mcp_write_audit.jsonl")

    report = build_retention_report(tmp_path, stale_after_days=30.0)
    summary = build_review_required_summary(report)

    assert summary.review_required_count == 1
    paths = {entry.path for entry in summary.entries}
    assert paths == {"readiness_report.json"}


def test_rotate_no_dry_run_skips_protected_stale_artifacts(tmp_path: Path) -> None:
    protected = _make_artifact(tmp_path, "mcp_write_audit.jsonl")
    rotatable = _make_artifact(tmp_path, "benchmark.json")
    _age_file(protected, 60.0)
    _age_file(rotatable, 60.0)

    summary = rotate_stale_artifacts(tmp_path, stale_after_days=30.0, dry_run=False)

    assert summary.archived_count == 1
    assert summary.skipped_count >= 1
    assert protected.exists()
    assert not rotatable.exists()
    archived_files = list((tmp_path / ARCHIVE_SUBDIR).rglob("benchmark.json"))
    assert len(archived_files) == 1


def test_save_cleanup_protected_and_review_summaries_write_json(tmp_path: Path) -> None:
    # readiness_report.json stale -> rotatable (operational class)
    stale_report = _make_artifact(tmp_path, "readiness_report.json")
    _age_file(stale_report, 35.0)
    _make_artifact(tmp_path, "mcp_write_audit.jsonl")   # protected
    _make_artifact(tmp_path, "unknown_custom.json")      # unknown -> review_required
    report = build_retention_report(tmp_path, stale_after_days=30.0)

    cleanup_out = tmp_path / "cleanup.json"
    protected_out = tmp_path / "protected.json"
    review_out = tmp_path / "review.json"
    cleanup_saved = save_cleanup_eligibility_summary(
        build_cleanup_eligibility_summary(report),
        cleanup_out,
    )
    protected_saved = save_protected_artifact_summary(
        build_protected_artifact_summary(report),
        protected_out,
    )
    review_saved = save_review_required_summary(
        build_review_required_summary(report),
        review_out,
    )

    assert cleanup_saved == cleanup_out
    assert protected_saved == protected_out
    assert review_saved == review_out
    cleanup_data = json.loads(cleanup_out.read_text(encoding="utf-8"))
    protected_data = json.loads(protected_out.read_text(encoding="utf-8"))
    review_data = json.loads(review_out.read_text(encoding="utf-8"))
    assert cleanup_data["report_type"] == "cleanup_eligibility_summary"
    assert cleanup_data["dry_run_default"] is True
    assert protected_data["report_type"] == "protected_artifact_summary"
    assert protected_data["protected_count"] == 1
    assert review_data["report_type"] == "review_required_artifact_summary"
    assert review_data["review_required_count"] == 1
    assert review_data["entries"][0]["retention_rationale"]
    assert review_data["entries"][0]["operator_guidance"]

