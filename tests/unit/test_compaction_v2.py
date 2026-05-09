"""Tests für inkrementelle JSONL→DuckDB-Compaction (ADR 0003 Phase 3)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest

from app.storage.compaction_v2 import (
    CompactionSource,
    compact_source,
    default_sources,
    handle_alert_audit_row,
    handle_alert_outcome_row,
    handle_paper_execution_row,
    run_full_compaction,
)
from app.storage.duckdb_migrate import apply_migrations

_REAL_MIGRATIONS = Path(__file__).resolve().parents[2] / "app" / "storage" / "duckdb_migrations"


# --- Helpers -------------------------------------------------------------


@pytest.fixture
def db(tmp_path: Path) -> duckdb.DuckDBPyConnection:
    db_path = tmp_path / "test.duckdb"
    con = duckdb.connect(str(db_path))
    apply_migrations(con, migrations_dir=_REAL_MIGRATIONS)
    yield con
    con.close()


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def _paper_fill(fill_id: str, *, ts: str = "2026-05-09T12:00:00+00:00", **overrides) -> dict:
    base = {
        "schema_version": "v2",
        "event_type": "order_filled",
        "timestamp_utc": ts,
        "fill_id": fill_id,
        "order_id": f"ord-{fill_id}",
        "symbol": "BTC/USDT",
        "side": "buy",
        "position_side": "long",
        "quantity": 0.5,
        "fill_price": 50000.0,
        "fee_usd": 25.0,
        "pnl_usd": 0.0,
    }
    base.update(overrides)
    return base


def _alert_audit(doc_id: str, *, ts: str = "2026-05-09T12:00:00+00:00", **overrides) -> dict:
    base = {
        "document_id": doc_id,
        "channel": "telegram",
        "message_id": "msg-1",
        "is_digest": False,
        "dispatched_at": ts,
        "sentiment_label": "bullish",
        "affected_assets": ["BTC/USDT"],
        "priority": 8,
        "actionable": True,
        "source_name": "rss",
    }
    base.update(overrides)
    return base


def _alert_outcome(doc_id: str, outcome: str, *, ts: str = "2026-05-09T13:00:00+00:00") -> dict:
    return {
        "document_id": doc_id,
        "outcome": outcome,
        "annotated_at": ts,
        "asset": "BTC/USDT",
    }


# --- Stream-Parse -------------------------------------------------------


def test_compact_empty_jsonl_advances_no_offset(tmp_path: Path, db: duckdb.DuckDBPyConnection) -> None:
    jsonl = tmp_path / "empty.jsonl"
    jsonl.touch()
    src = CompactionSource("alert_audit", jsonl, handle_alert_audit_row)

    result = compact_source(db, src)

    assert result.rows_read == 0
    assert result.rows_applied == 0
    assert result.bytes_advanced == 0
    assert result.error is None


def test_compact_missing_file_is_no_error(tmp_path: Path, db: duckdb.DuckDBPyConnection) -> None:
    src = CompactionSource("alert_audit", tmp_path / "nope.jsonl", handle_alert_audit_row)
    result = compact_source(db, src)
    assert result.rows_read == 0
    assert result.rows_applied == 0
    assert result.error is None


def test_compact_paper_fill_inserts_one_trade(tmp_path: Path, db: duckdb.DuckDBPyConnection) -> None:
    jsonl = tmp_path / "paper.jsonl"
    _write_jsonl(jsonl, [_paper_fill("fill_aaa")])
    src = CompactionSource("paper_execution_audit", jsonl, handle_paper_execution_row)

    result = compact_source(db, src)

    assert result.rows_read == 1
    assert result.rows_applied == 1
    assert result.last_event_id == "fill_aaa"
    n = db.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    assert n == 1


def test_paper_compaction_skips_non_fill_event_types(
    tmp_path: Path, db: duckdb.DuckDBPyConnection
) -> None:
    jsonl = tmp_path / "paper.jsonl"
    _write_jsonl(
        jsonl,
        [
            _paper_fill("fill_a"),
            {
                "schema_version": "v2",
                "event_type": "position_tp_tiers_set",
                "timestamp_utc": "2026-05-09T12:01:00+00:00",
                "symbol": "BTC/USDT",
                "tiers": [{"price": 51000.0, "qty_share": 0.5}],
            },
            _paper_fill("fill_b", event_type="position_closed", pnl_usd=120.5),
        ],
    )
    src = CompactionSource("paper_execution_audit", jsonl, handle_paper_execution_row)
    result = compact_source(db, src)

    assert result.rows_read == 3
    assert result.rows_applied == 2  # tp_tiers_set wird ignoriert
    rows = db.execute("SELECT fill_id, event_type, pnl_usd FROM trades ORDER BY ts").fetchall()
    assert {r[0] for r in rows} == {"fill_a", "fill_b"}
    assert any(r[1] == "position_closed" and r[2] == 120.5 for r in rows)


def test_compaction_is_idempotent_across_re_runs(
    tmp_path: Path, db: duckdb.DuckDBPyConnection
) -> None:
    jsonl = tmp_path / "paper.jsonl"
    _write_jsonl(jsonl, [_paper_fill(f"fill_{i}") for i in range(5)])
    src = CompactionSource("paper_execution_audit", jsonl, handle_paper_execution_row)

    r1 = compact_source(db, src)
    r2 = compact_source(db, src)
    r3 = compact_source(db, src)

    assert r1.rows_applied == 5
    assert r2.rows_applied == 0
    assert r3.rows_applied == 0
    assert db.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 5


def test_compaction_picks_up_appended_rows_only(
    tmp_path: Path, db: duckdb.DuckDBPyConnection
) -> None:
    jsonl = tmp_path / "paper.jsonl"
    _write_jsonl(jsonl, [_paper_fill(f"fill_{i}") for i in range(3)])
    src = CompactionSource("paper_execution_audit", jsonl, handle_paper_execution_row)

    r1 = compact_source(db, src)
    assert r1.rows_applied == 3

    _write_jsonl(jsonl, [_paper_fill(f"fill_{i}") for i in range(3, 7)])
    r2 = compact_source(db, src)

    assert r2.rows_applied == 4
    assert db.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 7


def test_compaction_tolerates_partial_last_line(
    tmp_path: Path, db: duckdb.DuckDBPyConnection
) -> None:
    """Letzte Zeile ohne \\n = mid-write durch Writer; darf nicht konsumiert werden."""
    jsonl = tmp_path / "paper.jsonl"
    _write_jsonl(jsonl, [_paper_fill("fill_complete")])
    # append a partial line WITHOUT trailing newline
    with jsonl.open("ab") as f:
        f.write(b'{"schema_version": "v2", "event_type": "order_filled", "fill_id": "fill_partial')

    src = CompactionSource("paper_execution_audit", jsonl, handle_paper_execution_row)
    r1 = compact_source(db, src)

    assert r1.rows_applied == 1  # nur fill_complete
    n = db.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    assert n == 1

    # Now writer finishes the line — re-run should pick up the rest.
    with jsonl.open("ab") as f:
        f.write(b'", "order_id": "ord_partial", "symbol": "ETH/USDT", "side": "buy", '
                b'"position_side": "long", "quantity": 1.0, "fill_price": 3000.0, '
                b'"fee_usd": 1.5, "pnl_usd": 0.0, "timestamp_utc": "2026-05-09T12:05:00+00:00"}\n')

    r2 = compact_source(db, src)
    assert r2.rows_applied == 1
    assert db.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 2


def test_compaction_skips_corrupted_mid_file_line(
    tmp_path: Path, db: duckdb.DuckDBPyConnection
) -> None:
    """Mid-file JSONDecodeError: count as invalid, advance offset, skip row."""
    jsonl = tmp_path / "paper.jsonl"
    _write_jsonl(jsonl, [_paper_fill("fill_a")])
    with jsonl.open("ab") as f:
        f.write(b'{this is not json}\n')
    _write_jsonl(jsonl, [_paper_fill("fill_b")])

    src = CompactionSource("paper_execution_audit", jsonl, handle_paper_execution_row)
    result = compact_source(db, src)

    assert result.rows_read == 3
    assert result.rows_applied == 2  # fill_a + fill_b
    assert result.rows_invalid == 1
    rows = db.execute("SELECT fill_id FROM trades ORDER BY fill_id").fetchall()
    assert [r[0] for r in rows] == ["fill_a", "fill_b"]


def test_alert_audit_compaction_inserts_audit_row(
    tmp_path: Path, db: duckdb.DuckDBPyConnection
) -> None:
    jsonl = tmp_path / "alerts.jsonl"
    _write_jsonl(jsonl, [_alert_audit("doc-aaa")])
    src = CompactionSource("alert_audit", jsonl, handle_alert_audit_row)

    result = compact_source(db, src)

    assert result.rows_applied == 1
    rows = db.execute(
        "SELECT document_id, sentiment_label, priority, source_name, outcome FROM audits"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "doc-aaa"
    assert rows[0][1] == "bullish"
    assert rows[0][2] == 8
    assert rows[0][3] == "rss"
    assert rows[0][4] is None  # no outcome yet


def test_alert_outcome_updates_existing_audit(
    tmp_path: Path, db: duckdb.DuckDBPyConnection
) -> None:
    """Outcomes haben UPDATE-Pfad; setzen outcome+annotated_at auf existierende rows."""
    audits_jsonl = tmp_path / "audits.jsonl"
    outcomes_jsonl = tmp_path / "outcomes.jsonl"
    _write_jsonl(audits_jsonl, [_alert_audit("doc-aaa")])
    _write_jsonl(outcomes_jsonl, [_alert_outcome("doc-aaa", "hit")])

    audit_src = CompactionSource("alert_audit", audits_jsonl, handle_alert_audit_row)
    outcome_src = CompactionSource("alert_outcomes", outcomes_jsonl, handle_alert_outcome_row)

    compact_source(db, audit_src)
    compact_source(db, outcome_src)

    row = db.execute(
        "SELECT outcome, annotated_at FROM audits WHERE document_id = 'doc-aaa'"
    ).fetchone()
    assert row[0] == "hit"
    assert row[1] is not None


def test_alert_outcome_with_unknown_doc_id_is_noop(
    tmp_path: Path, db: duckdb.DuckDBPyConnection
) -> None:
    """UPDATE auf nicht-existierenden document_id ist OK (UPDATE rowcount=0)."""
    outcomes_jsonl = tmp_path / "outcomes.jsonl"
    _write_jsonl(outcomes_jsonl, [_alert_outcome("doc-ghost", "miss")])
    src = CompactionSource("alert_outcomes", outcomes_jsonl, handle_alert_outcome_row)

    result = compact_source(db, src)

    assert result.rows_applied == 1  # logical apply (UPDATE ran)
    assert result.error is None


def test_alert_outcome_rejects_invalid_outcome_label(
    tmp_path: Path, db: duckdb.DuckDBPyConnection
) -> None:
    outcomes_jsonl = tmp_path / "outcomes.jsonl"
    _write_jsonl(
        outcomes_jsonl,
        [
            {"document_id": "doc-x", "outcome": "definitely-not-valid", "annotated_at": "2026-05-09T12:00:00+00:00"},
        ],
    )
    src = CompactionSource("alert_outcomes", outcomes_jsonl, handle_alert_outcome_row)

    result = compact_source(db, src)

    assert result.rows_applied == 0  # invalid outcome, skipped


def test_full_compaction_runs_all_sources_in_order(
    tmp_path: Path, db: duckdb.DuckDBPyConnection
) -> None:
    """Default-3-Sources werden sequentiell ausgeführt; outcomes nach audits."""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write_jsonl(artifacts / "paper_execution_audit.jsonl", [_paper_fill("fill_a")])
    _write_jsonl(artifacts / "alert_audit.jsonl", [_alert_audit("doc-aaa")])
    _write_jsonl(artifacts / "alert_outcomes.jsonl", [_alert_outcome("doc-aaa", "hit")])

    results = run_full_compaction(db, artifacts)

    assert set(results.keys()) == {"paper_execution_audit", "alert_audit", "alert_outcomes"}
    assert results["paper_execution_audit"].rows_applied == 1
    assert results["alert_audit"].rows_applied == 1
    assert results["alert_outcomes"].rows_applied == 1
    # Outcome must be applied to the audit row
    outcome = db.execute("SELECT outcome FROM audits WHERE document_id = 'doc-aaa'").fetchone()
    assert outcome[0] == "hit"


def test_watermark_persists_across_runs(
    tmp_path: Path, db: duckdb.DuckDBPyConnection
) -> None:
    jsonl = tmp_path / "paper.jsonl"
    _write_jsonl(jsonl, [_paper_fill(f"fill_{i}") for i in range(3)])
    src = CompactionSource("paper_execution_audit", jsonl, handle_paper_execution_row)

    compact_source(db, src)

    wm = db.execute(
        "SELECT last_byte_offset, rows_processed, last_event_id "
        "FROM _compaction_watermark WHERE source = 'paper_execution_audit'"
    ).fetchone()
    file_size = jsonl.stat().st_size
    assert wm[0] == file_size
    assert wm[1] == 3
    assert wm[2] in {f"fill_{i}" for i in range(3)}


def test_default_sources_returns_three_known_paths(tmp_path: Path) -> None:
    sources = default_sources(tmp_path / "artifacts")
    names = {s.name for s in sources}
    assert names == {"paper_execution_audit", "alert_audit", "alert_outcomes"}
    paths = {s.jsonl_path.name for s in sources}
    assert paths == {
        "paper_execution_audit.jsonl",
        "alert_audit.jsonl",
        "alert_outcomes.jsonl",
    }


def test_paper_fill_with_invalid_quantity_skipped(
    tmp_path: Path, db: duckdb.DuckDBPyConnection
) -> None:
    jsonl = tmp_path / "paper.jsonl"
    _write_jsonl(
        jsonl,
        [
            _paper_fill("fill_ok"),
            _paper_fill("fill_nan", quantity="not-a-number"),
            _paper_fill("fill_inf", fill_price=float("inf")),
        ],
    )
    src = CompactionSource("paper_execution_audit", jsonl, handle_paper_execution_row)
    result = compact_source(db, src)

    assert result.rows_read == 3
    assert result.rows_applied == 1  # only fill_ok
    rows = db.execute("SELECT fill_id FROM trades").fetchall()
    assert [r[0] for r in rows] == ["fill_ok"]


def test_compaction_handles_appended_outcome_after_initial_run(
    tmp_path: Path, db: duckdb.DuckDBPyConnection
) -> None:
    """audit dispatched at T0 → run → outcome appended at T1 → re-run → outcome applied."""
    audits_jsonl = tmp_path / "audits.jsonl"
    outcomes_jsonl = tmp_path / "outcomes.jsonl"
    _write_jsonl(audits_jsonl, [_alert_audit("doc-bbb")])

    audit_src = CompactionSource("alert_audit", audits_jsonl, handle_alert_audit_row)
    outcome_src = CompactionSource("alert_outcomes", outcomes_jsonl, handle_alert_outcome_row)

    compact_source(db, audit_src)
    r0 = compact_source(db, outcome_src)
    assert r0.rows_applied == 0  # no outcomes yet

    _write_jsonl(outcomes_jsonl, [_alert_outcome("doc-bbb", "miss")])
    r1 = compact_source(db, outcome_src)
    assert r1.rows_applied == 1
    outcome = db.execute("SELECT outcome FROM audits WHERE document_id = 'doc-bbb'").fetchone()
    assert outcome[0] == "miss"
