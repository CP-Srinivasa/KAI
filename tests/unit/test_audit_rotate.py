"""Audit-stream rotation contract (Sprint S5): archiving, tail-preserving,
fail-safe direction, hard exclusions.

Behaviour, not implementation: nothing is ever deleted; the live file keeps its
recent tail; dry-run never mutates; the engine replay-SSOT is not on the
allowlist.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import audit_rotate  # noqa: E402


def _write_lines(path: Path, n: int, pad: int = 100) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for i in range(n):
            fh.write(f'{{"i": {i}, "pad": "{"x" * pad}"}}\n')


def test_under_threshold_is_noop(tmp_path: Path) -> None:
    f = tmp_path / "stream.jsonl"
    _write_lines(f, 10)
    result = audit_rotate.rotate_stream(
        f, max_bytes=10_000_000, keep_lines=5, archive_dir=tmp_path / "archive", apply=True
    )
    assert result.rotated is False
    assert result.reason == "under_threshold"
    assert f.read_text(encoding="utf-8").count("\n") == 10


def test_dry_run_never_mutates(tmp_path: Path) -> None:
    f = tmp_path / "stream.jsonl"
    _write_lines(f, 100)
    before = f.read_text(encoding="utf-8")
    result = audit_rotate.rotate_stream(
        f, max_bytes=100, keep_lines=5, archive_dir=tmp_path / "archive", apply=False
    )
    assert result.rotated is False
    assert result.reason.startswith("dry_run_would_rotate_to:")
    assert f.read_text(encoding="utf-8") == before
    assert not (tmp_path / "archive").exists()


def test_rotation_archives_full_history_and_keeps_tail(tmp_path: Path) -> None:
    f = tmp_path / "stream.jsonl"
    _write_lines(f, 100)
    full = f.read_text(encoding="utf-8")
    result = audit_rotate.rotate_stream(
        f, max_bytes=100, keep_lines=10, archive_dir=tmp_path / "archive", apply=True
    )
    assert result.rotated is True
    assert result.kept_lines == 10
    # archive holds the COMPLETE pre-rotation history (nothing deleted)
    archived = Path(result.archive_path).read_text(encoding="utf-8")
    assert archived == full
    # live file is exactly the last 10 lines, order preserved
    live = f.read_text(encoding="utf-8").splitlines()
    assert len(live) == 10
    assert '"i": 99' in live[-1]
    assert '"i": 90' in live[0]


def test_missing_file_is_noop(tmp_path: Path) -> None:
    result = audit_rotate.rotate_stream(
        tmp_path / "nope.jsonl",
        max_bytes=1,
        keep_lines=1,
        archive_dir=tmp_path / "archive",
        apply=True,
    )
    assert result.rotated is False
    assert result.reason == "missing"


def test_engine_replay_ssot_is_hard_excluded() -> None:
    """paper_execution_audit.jsonl is the PaperExecutionEngine replay source —
    it must NEVER appear on the rotation allowlist."""
    names = {rule.filename for rule in audit_rotate.ROTATION_RULES}
    assert "paper_execution_audit.jsonl" not in names
    assert "blocked_outcomes.jsonl" not in names


def test_run_respects_allowlist_only(tmp_path: Path) -> None:
    # an oversized NON-allowlisted stream must stay untouched
    rogue = tmp_path / "paper_execution_audit.jsonl"
    _write_lines(rogue, 50)
    allow = tmp_path / "bridge_pending_orders.jsonl"
    _write_lines(allow, 50)
    results = audit_rotate.run(tmp_path, apply=True)  # thresholds are 20MB → no-ops
    assert all(r.rotated is False for r in results)
    assert rogue.exists() and allow.exists()
