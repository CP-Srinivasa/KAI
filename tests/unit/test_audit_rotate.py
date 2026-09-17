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


def test_api_request_audit_is_rotated() -> None:
    """Der 127-MB-Brocken (Befund 2026-07-30) gehört auf die Allowlist.

    Korrektur 2026-09-17: er hat einen Leser — ``back_edge_evaluator`` sucht im
    LIVE-File Dashboard-Handlungen im 24-h-Reaktionsfenster. Deshalb rotiert er
    nach Zeitfenster, nicht nach Zeilenzahl (siehe Tests unten).
    """
    names = {rule.filename for rule in audit_rotate.ROTATION_RULES}
    assert "api_request_audit.jsonl" in names


def test_trading_loop_audit_stays_excluded_despite_its_size() -> None:
    """54,8 MB, aber NICHT rotierbar — Grösse ist kein Freifahrtschein.

    Geprüft 2026-07-30: ``load_trading_loop_cycles`` wird von daily_briefing,
    health_check, loop_idle_signal und canonical_read über die VOLLE Historie
    aufgerufen. Eine Rotation würde deren Eingaben still verändern. Der richtige
    Hebel für die Lesekosten ist der Fenster-Read (``iter_jsonl_since``), nicht
    das Kürzen des Streams.
    """
    names = {rule.filename for rule in audit_rotate.ROTATION_RULES}
    assert "trading_loop_audit.jsonl" not in names


def test_run_respects_allowlist_only(tmp_path: Path) -> None:
    # an oversized NON-allowlisted stream must stay untouched
    rogue = tmp_path / "paper_execution_audit.jsonl"
    _write_lines(rogue, 50)
    allow = tmp_path / "bridge_pending_orders.jsonl"
    _write_lines(allow, 50)
    results = audit_rotate.run(tmp_path, apply=True)  # thresholds are 20MB → no-ops
    assert all(r.rotated is False for r in results)
    assert rogue.exists() and allow.exists()


def test_no_shrink_guard_skips_when_tail_covers_whole_file(tmp_path: Path) -> None:
    """Calibration guard (first live run 2026-06-11): when keep_lines >= total
    lines, rotation would archive a full copy WITHOUT shrinking the live file
    every run — it must skip with an actionable reason instead."""
    f = tmp_path / "stream.jsonl"
    _write_lines(f, 50)
    before = f.read_text(encoding="utf-8")
    result = audit_rotate.rotate_stream(
        f, max_bytes=100, keep_lines=50, archive_dir=tmp_path / "archive", apply=True
    )
    assert result.rotated is False
    assert result.reason.startswith("tail_covers_whole_file:lines=50<=keep_lines=50")
    assert f.read_text(encoding="utf-8") == before
    assert not (tmp_path / "archive").exists()


def _write_stamped(path: Path, stamps: list[str]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for i, stamp in enumerate(stamps):
            fh.write(f'{{"timestamp_utc": "{stamp}", "i": {i}, "pad": "{"x" * 100}"}}\n')


def test_time_window_keeps_every_line_inside_the_window(tmp_path: Path) -> None:
    """Die Rate darf das Fenster nicht bestimmen: alle Zeilen der letzten
    ``keep_hours`` bleiben, egal wie viele es sind (unterhalb der Kappe)."""
    from datetime import UTC, datetime, timedelta

    now = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
    old = [(now - timedelta(hours=200, minutes=i)).isoformat() for i in range(50, 0, -1)]
    recent = [(now - timedelta(hours=100, minutes=-i)).isoformat() for i in range(30)]
    f = tmp_path / "api_request_audit.jsonl"
    _write_stamped(f, old + recent)
    result = audit_rotate.rotate_stream(
        f,
        max_bytes=100,
        keep_lines=1_000,
        keep_hours=168,
        timestamp_key="timestamp_utc",
        archive_dir=tmp_path / "archive",
        apply=True,
        now=now,
    )
    assert result.rotated is True
    assert result.kept_lines == 30
    kept = f.read_text(encoding="utf-8").splitlines()
    assert all('"i": ' in line for line in kept)
    assert kept[0].startswith(f'{{"timestamp_utc": "{recent[0]}"')


def test_time_window_is_capped_under_a_flood(tmp_path: Path) -> None:
    """Flut wie am 16.09.: das Fenster wird durch ``keep_lines`` gedeckelt, damit
    das Live-File nicht selbst zum Archiv wird."""
    from datetime import UTC, datetime, timedelta

    now = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
    stamps = [(now - timedelta(seconds=500 - i)).isoformat() for i in range(500)]
    f = tmp_path / "api_request_audit.jsonl"
    _write_stamped(f, stamps)
    result = audit_rotate.rotate_stream(
        f,
        max_bytes=100,
        keep_lines=100,
        keep_hours=168,
        timestamp_key="timestamp_utc",
        archive_dir=tmp_path / "archive",
        apply=True,
        now=now,
    )
    assert result.rotated is True
    assert result.kept_lines == 100
    assert (
        f.read_text(encoding="utf-8")
        .splitlines()[-1]
        .startswith(f'{{"timestamp_utc": "{stamps[-1]}"')
    )


def test_time_window_that_covers_everything_does_not_rotate(tmp_path: Path) -> None:
    from datetime import UTC, datetime, timedelta

    now = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
    f = tmp_path / "api_request_audit.jsonl"
    _write_stamped(f, [(now - timedelta(hours=1)).isoformat()] * 20)
    result = audit_rotate.rotate_stream(
        f,
        max_bytes=100,
        keep_lines=1_000,
        keep_hours=168,
        timestamp_key="timestamp_utc",
        archive_dir=tmp_path / "archive",
        apply=True,
        now=now,
    )
    assert result.rotated is False
    assert result.reason.startswith("tail_covers_whole_file")
    assert not (tmp_path / "archive").exists()


def test_unreadable_lines_are_kept_not_treated_as_old(tmp_path: Path) -> None:
    from datetime import UTC, datetime, timedelta

    now = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
    f = tmp_path / "api_request_audit.jsonl"
    _write_stamped(f, [(now - timedelta(hours=300)).isoformat()] * 10)
    with f.open("a", encoding="utf-8") as fh:
        fh.write("kaputt\n")
    result = audit_rotate.rotate_stream(
        f,
        max_bytes=100,
        keep_lines=1_000,
        keep_hours=168,
        timestamp_key="timestamp_utc",
        archive_dir=tmp_path / "archive",
        apply=True,
        now=now,
    )
    assert result.rotated is True
    assert f.read_text(encoding="utf-8") == "kaputt\n"


def test_api_request_audit_window_covers_the_back_edge_reaction_window() -> None:
    """Der einzige Leser braucht 24 h im LIVE-File — das Fenster muss darüber liegen."""
    from app.observability.operator_feedback import REACTION_WINDOW_HOURS

    rule = next(r for r in audit_rotate.ROTATION_RULES if r.filename == "api_request_audit.jsonl")
    assert rule.timestamp_key == "timestamp_utc"
    assert rule.keep_hours is not None and rule.keep_hours >= 2 * REACTION_WINDOW_HOURS
