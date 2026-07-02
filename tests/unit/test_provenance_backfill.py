"""D-125 — provenance backfill for legacy JSONL rows.

Invariants:
- Legacy rows (no ``provenance`` key) get augmented with a best-effort
  SignalProvenance; TV-prefixed docs resolve to ``tradingview_webhook``,
  RSS rows resolve via the supplied ``source_by_doc`` map.
- Idempotent: a second run is a no-op on rows already tagged.
- Concurrent-write guard: mtime bump between scan and rewrite aborts the
  rewrite and leaves the file untouched (no .bak, no corruption).
- Dry-run: returns the same counts without touching the file or writing .bak.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from app.alerts.provenance_backfill import backfill_provenance

SECRET = "backfill-unit-test"


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def test_backfill_augments_rss_and_tv_rows(tmp_path: Path) -> None:
    audit_path = tmp_path / "alert_audit.jsonl"
    outcomes_path = tmp_path / "alert_outcomes.jsonl"
    tv_pending_path = tmp_path / "tradingview_pending_signals.jsonl"

    _write_jsonl(
        audit_path,
        [
            # RSS row with source in the DB map
            {
                "document_id": "doc_rss_1",
                "channel": "telegram",
                "message_id": None,
                "is_digest": False,
                "dispatched_at": "2026-04-20T10:00:00+00:00",
            },
            # TV row with signal_path_id via pending map
            {
                "document_id": "tv:evt_123",
                "channel": "tradingview_webhook",
                "message_id": None,
                "is_digest": False,
                "dispatched_at": "2026-04-20T11:00:00+00:00",
            },
            # Legacy row where source DB map has no entry → no_source
            {
                "document_id": "doc_purged",
                "channel": "telegram",
                "message_id": None,
                "is_digest": False,
                "dispatched_at": "2026-04-20T12:00:00+00:00",
            },
        ],
    )
    _write_jsonl(
        outcomes_path,
        [
            {
                "document_id": "doc_rss_1",
                "outcome": "hit",
                "annotated_at": "2026-04-20T13:00:00+00:00",
            },
        ],
    )
    _write_jsonl(
        tv_pending_path,
        [
            {
                "event_id": "evt_123",
                "provenance": {"signal_path_id": "sp_tv_evt_123"},
            },
        ],
    )

    result = backfill_provenance(
        artifacts_dir=tmp_path,
        secret=SECRET,
        source_by_doc={"doc_rss_1": "cointelegraph"},
        dry_run=False,
    )

    # --- Counts
    audit_counts = result["alert_audit.jsonl"]
    assert audit_counts["total"] == 3
    assert audit_counts["augmented"] == 2  # rss + tv
    assert audit_counts["already_tagged"] == 0
    assert audit_counts["no_source"] == 1  # doc_purged

    outcome_counts = result["alert_outcomes.jsonl"]
    assert outcome_counts["augmented"] == 1
    assert outcome_counts["no_source"] == 0

    # --- File contents
    rows = _read_jsonl(audit_path)
    rss_row = next(r for r in rows if r["document_id"] == "doc_rss_1")
    assert rss_row["provenance"]["source"] == "cointelegraph"
    assert rss_row["provenance"]["version"] == "rss-1"
    assert rss_row["provenance"]["auth_method"] == "n/a"
    assert rss_row["provenance"]["ingest_event_id"] == "doc_rss_1"
    assert "provenance_hash" in rss_row["provenance"]

    tv_row = next(r for r in rows if r["document_id"] == "tv:evt_123")
    assert tv_row["provenance"]["source"] == "tradingview_webhook"
    assert tv_row["provenance"]["version"] == "tv-3"
    assert tv_row["provenance"]["signal_path_id"] == "sp_tv_evt_123"
    assert tv_row["provenance"]["ingest_event_id"] == "evt_123"

    purged_row = next(r for r in rows if r["document_id"] == "doc_purged")
    assert "provenance" not in purged_row  # source gate keeps it as "unknown"


def test_backfill_is_idempotent(tmp_path: Path) -> None:
    """Second run: all rows are already_tagged, no_source unchanged."""
    audit_path = tmp_path / "alert_audit.jsonl"
    _write_jsonl(
        audit_path,
        [
            {
                "document_id": "doc_1",
                "channel": "telegram",
                "message_id": None,
                "is_digest": False,
                "dispatched_at": "2026-04-20T10:00:00+00:00",
            },
        ],
    )

    first = backfill_provenance(
        artifacts_dir=tmp_path,
        secret=SECRET,
        source_by_doc={"doc_1": "rss_source"},
        dry_run=False,
    )
    assert first["alert_audit.jsonl"]["augmented"] == 1

    second = backfill_provenance(
        artifacts_dir=tmp_path,
        secret=SECRET,
        source_by_doc={"doc_1": "rss_source"},
        dry_run=False,
    )
    assert second["alert_audit.jsonl"]["augmented"] == 0
    assert second["alert_audit.jsonl"]["already_tagged"] == 1


def test_backfill_dry_run_does_not_write(tmp_path: Path) -> None:
    audit_path = tmp_path / "alert_audit.jsonl"
    original = {
        "document_id": "doc_1",
        "channel": "telegram",
        "message_id": None,
        "is_digest": False,
        "dispatched_at": "2026-04-20T10:00:00+00:00",
    }
    _write_jsonl(audit_path, [original])

    result = backfill_provenance(
        artifacts_dir=tmp_path,
        secret=SECRET,
        source_by_doc={"doc_1": "rss_source"},
        dry_run=True,
    )
    assert result["alert_audit.jsonl"]["augmented"] == 1

    # file is byte-identical to pre-run, and no .bak exists
    assert _read_jsonl(audit_path) == [original]
    assert not list(tmp_path.glob("alert_audit.jsonl.bak.*"))


def test_backfill_missing_files_returns_zero_counts(tmp_path: Path) -> None:
    """No audit/outcomes files → counts are all zero, no crash."""
    result = backfill_provenance(
        artifacts_dir=tmp_path,
        secret=SECRET,
        source_by_doc={},
        dry_run=False,
    )
    for counts in result.values():
        assert counts["total"] == 0
        assert counts["augmented"] == 0


def test_backfill_writes_timestamped_backup(tmp_path: Path) -> None:
    audit_path = tmp_path / "alert_audit.jsonl"
    _write_jsonl(
        audit_path,
        [
            {
                "document_id": "doc_1",
                "channel": "telegram",
                "message_id": None,
                "is_digest": False,
                "dispatched_at": "2026-04-20T10:00:00+00:00",
            }
        ],
    )

    backfill_provenance(
        artifacts_dir=tmp_path,
        secret=SECRET,
        source_by_doc={"doc_1": "rss_source"},
        dry_run=False,
    )

    backups = list(tmp_path.glob("alert_audit.jsonl.bak.*"))
    assert len(backups) == 1
    # backup must hold the pre-rewrite row (no provenance yet)
    backup_rows = _read_jsonl(backups[0])
    assert "provenance" not in backup_rows[0]


def test_quality_bar_uses_persisted_provenance_over_db_lookup(tmp_path: Path) -> None:
    """Regression guard: persisted provenance wins over the DB-join fallback.

    Mirrors the quality-bar consumer invariant in provenance_metrics: once
    rows carry a persisted ``provenance`` field, the source resolution
    pipeline no longer depends on analysis-time DB joins.
    """
    from app.alerts.provenance_metrics import build_provenance_split_report

    audit_path = tmp_path / "alert_audit.jsonl"
    outcomes_path = tmp_path / "alert_outcomes.jsonl"
    tv_pending_path = tmp_path / "tradingview_pending_signals.jsonl"

    # Row carries persisted provenance="rss_A"; DB-join would say "rss_B".
    # Persisted value must win.
    _write_jsonl(
        audit_path,
        [
            {
                "document_id": "doc_1",
                "channel": "telegram",
                "message_id": None,
                "is_digest": False,
                "dispatched_at": "2026-04-20T10:00:00+00:00",
                "sentiment_label": "bullish",
                "affected_assets": ["BTC"],
                "provenance": {
                    "source": "rss_A",
                    "version": "rss-1",
                    "ingest_event_id": "doc_1",
                },
            },
        ],
    )
    _write_jsonl(
        outcomes_path,
        [{"document_id": "doc_1", "outcome": "hit", "annotated_at": "2026-04-20T11:00:00+00:00"}],
    )
    tv_pending_path.write_text("", encoding="utf-8")

    report = build_provenance_split_report(
        alert_audit_path=audit_path,
        alert_outcomes_path=outcomes_path,
        tradingview_pending_signals_path=tv_pending_path,
        source_by_doc={"doc_1": "rss_B"},  # DB-join should be ignored
    )

    sources = {m.source for m in report.by_source}
    assert "rss_a" in sources  # persisted wins, normalised to lowercase
    assert "rss_b" not in sources


def test_backfill_aborts_on_concurrent_write(tmp_path: Path, monkeypatch) -> None:
    """mtime bump between scan and rewrite → abort, original preserved.

    Patches ``Path.read_text`` on the audit file so that after the first
    read (which populates the row list) the mtime is bumped, simulating an
    external appender racing with the backfill.
    """
    import os as _os

    audit_path = tmp_path / "alert_audit.jsonl"
    original_row = {
        "document_id": "doc_1",
        "channel": "telegram",
        "message_id": None,
        "is_digest": False,
        "dispatched_at": "2026-04-20T10:00:00+00:00",
    }
    _write_jsonl(audit_path, [original_row])

    real_read_text = Path.read_text

    def _racing_read_text(self: Path, *args, **kwargs):  # type: ignore[override]
        result = real_read_text(self, *args, **kwargs)
        if self == audit_path:
            stat = audit_path.stat()
            # Bump mtime by 10s so the post-check trips.
            _os.utime(audit_path, (stat.st_atime, stat.st_mtime + 10.0))
        return result

    monkeypatch.setattr(Path, "read_text", _racing_read_text)

    result = backfill_provenance(
        artifacts_dir=tmp_path,
        secret=SECRET,
        source_by_doc={"doc_1": "rss_source"},
        dry_run=False,
    )

    audit_result = result["alert_audit.jsonl"]
    assert audit_result.get("aborted_concurrent_write") == 1
    # Original file content must be untouched — no provenance written, no .bak.
    assert _read_jsonl(audit_path) == [original_row]
    assert not list(tmp_path.glob("alert_audit.jsonl.bak.*"))
    _ = time  # keep import used
