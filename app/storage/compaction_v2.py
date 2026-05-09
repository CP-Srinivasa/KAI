"""Inkrementelle JSONL→DuckDB Compaction (ADR 0003 Phase 3).

Ersetzt das ``CREATE OR REPLACE TABLE``-Polling-Antipattern aus
``app/storage/analytics_db.py:run_compaction`` (Architect-Finding
ART-DUCKDB-001) durch eine watermark-basierte inkrementelle
Compaction:

* Pro JSONL-Source ein Eintrag in ``_compaction_watermark`` mit
  ``last_byte_offset``, ``last_event_id``, ``rows_processed``.
* Beim Run wird ``seek(last_byte_offset)`` aufgerufen, neue Zeilen
  werden tolerant geparst (siehe :mod:`app.storage.jsonl_io`),
  per ``INSERT OR IGNORE`` in die Ziel-Tabelle geschrieben und der
  Watermark wird in derselben DuckDB-Transaction aktualisiert.
* Crash-safe: Transaction-Rollback bei Fehler; INSERT OR IGNORE +
  Watermark-Re-Read sichert Idempotency bei Wiederholung.

Architektur (ADR 0003):
    JSONL (immutable WAL, append-only, portalocker LOCK_EX)
        │
        ▼
    Compactor.run() — single-writer (kai-compaction-worker.service)
        │  read seek(watermark) → stream-parse → INSERT OR IGNORE →
        │  update watermark → COMMIT
        ▼
    DuckDB (analytical read-layer, all readers in read_only=True)
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

logger = logging.getLogger(__name__)


# --- Source-Definition ---------------------------------------------------


@dataclass(frozen=True)
class CompactionSource:
    """Mapping einer JSONL-Quelle auf eine DuckDB-Tabelle."""

    name: str  # logical source key in _compaction_watermark
    jsonl_path: Path  # absolute or repo-relative path
    handler: Callable[[duckdb.DuckDBPyConnection, dict[str, Any]], int]
    """Returns 1 if the row was applied (insert/update), 0 if skipped."""


@dataclass(frozen=True)
class CompactionResult:
    """Per-source-Ergebnis eines Compaction-Runs."""

    source: str
    rows_read: int  # JSON-Lines geparst (inkl. skips)
    rows_applied: int  # davon wirklich INSERT/UPDATE
    rows_invalid: int  # JSONDecodeError oder Schema-Fehler
    bytes_advanced: int  # last_byte_offset-Delta
    last_event_id: str | None
    error: str | None = None


# --- Row-Handler pro Source ---------------------------------------------

_PAPER_FILL_EVENT_TYPES = {"order_filled", "position_closed", "position_partial_closed"}


def handle_paper_execution_row(con: duckdb.DuckDBPyConnection, row: dict[str, Any]) -> int:
    """Map a paper_execution_audit.jsonl row → trades-Tabelle.

    Nur fill-/close-Events werden als trades materialisiert; alle
    anderen Event-Types (position_tp_tiers_set, position_opened ohne
    fill, etc.) werden bewusst übersprungen — die gehen später in
    eigene Tabellen (eigene Migration), nicht in ``trades``.
    """
    event_type = row.get("event_type")
    if event_type not in _PAPER_FILL_EVENT_TYPES:
        return 0

    fill_id = row.get("fill_id")
    if not isinstance(fill_id, str):
        return 0

    timestamp = _parse_iso(row.get("timestamp_utc")) or _parse_iso(row.get("filled_at"))
    if timestamp is None:
        return 0

    # paper_execution_audit nutzt symbol/side(buy/sell)/position_side(long/short).
    # In der trades-Tabelle ist `side` long/short — wir mappen position_side
    # bevorzugt, falls es fehlt fallback auf direction-aus-side.
    asset = row.get("symbol")
    position_side = row.get("position_side")
    side = row.get("side")
    canonical_side: str
    if isinstance(position_side, str) and position_side in ("long", "short"):
        canonical_side = position_side
    elif side == "buy":
        canonical_side = "long"
    elif side == "sell":
        canonical_side = "short"
    else:
        return 0  # unbekannte side, lieber droppen

    quantity = row.get("quantity")
    price = row.get("fill_price")
    if not isinstance(asset, str) or not _is_finite_number(quantity) or not _is_finite_number(price):
        return 0

    fee_usd = row.get("fee_usd") if _is_finite_number(row.get("fee_usd")) else 0.0
    pnl_usd = row.get("pnl_usd") if _is_finite_number(row.get("pnl_usd")) else None
    schema_version = str(row.get("schema_version") or "v2")
    order_id = row.get("order_id") or fill_id
    source_tag = row.get("source_tag")

    res = con.execute(
        """
        INSERT OR IGNORE INTO trades(
            fill_id, order_id, asset, side, quantity, price, fee_usd, pnl_usd,
            source_tag, event_type, ts, schema_version, extras
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        """,
        [
            fill_id,
            order_id,
            asset,
            canonical_side,
            float(quantity),
            float(price),
            float(fee_usd),
            None if pnl_usd is None else float(pnl_usd),
            source_tag if isinstance(source_tag, str) else None,
            event_type,
            timestamp,
            schema_version,
        ],
    )
    # DuckDB returns rowcount via fetchall on INSERT OR IGNORE; we treat any
    # successful execute as one applied row even if the IGNORE swallowed it,
    # because the compaction "applied" the row as far as the watermark cares.
    _ = res
    return 1


def handle_alert_audit_row(con: duckdb.DuckDBPyConnection, row: dict[str, Any]) -> int:
    """Map an alert_audit.jsonl row → audits-Tabelle (insert basis)."""
    document_id = row.get("document_id")
    dispatched_at = _parse_iso(row.get("dispatched_at"))
    if not isinstance(document_id, str) or dispatched_at is None:
        return 0

    audit_id = f"{document_id}:{dispatched_at.isoformat()}"
    sentiment = row.get("sentiment_label")
    priority = row.get("priority")
    actionable = row.get("actionable")
    directional_eligible = row.get("directional_eligible")
    affected_assets = row.get("affected_assets")
    source_name = row.get("source_name")
    is_digest = bool(row.get("is_digest", False))
    channel = row.get("channel") or "telegram"

    con.execute(
        """
        INSERT OR IGNORE INTO audits(
            audit_id, document_id, channel, sentiment_label, priority,
            actionable, directional_eligible, outcome, affected_assets,
            source_name, is_digest, dispatched_at, annotated_at,
            expected_signal_p, schema_version, extras
        ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, NULL, NULL, ?, NULL)
        """,
        [
            audit_id,
            document_id,
            str(channel),
            sentiment if isinstance(sentiment, str) else None,
            int(priority) if isinstance(priority, (int, float)) else None,
            bool(actionable) if isinstance(actionable, bool) else None,
            bool(directional_eligible) if isinstance(directional_eligible, bool) else None,
            json.dumps(affected_assets) if isinstance(affected_assets, list) else None,
            source_name if isinstance(source_name, str) else None,
            is_digest,
            dispatched_at,
            "v1",
        ],
    )
    return 1


def handle_alert_outcome_row(con: duckdb.DuckDBPyConnection, row: dict[str, Any]) -> int:
    """Map an alert_outcomes.jsonl row → UPDATE audits.outcome+annotated_at.

    Outcomes referenzieren einen früheren alert_audit-Eintrag via
    document_id. Wir aktualisieren ALLE matchenden audits-rows (es kann
    mehrere Dispatches pro document_id geben — Re-Send, Digest+Single).
    Der "latest annotation"-Pattern aus hold_metrics.py wird im SQL
    nachgebildet: spätere Outcomes überschreiben frühere via UPDATE.
    """
    document_id = row.get("document_id")
    outcome = row.get("outcome")
    annotated_at = _parse_iso(row.get("annotated_at"))

    if not isinstance(document_id, str) or not isinstance(outcome, str):
        return 0
    if outcome not in ("hit", "miss", "inconclusive"):
        return 0

    con.execute(
        """
        UPDATE audits
        SET outcome = ?, annotated_at = ?
        WHERE document_id = ?
        """,
        [outcome, annotated_at, document_id],
    )
    return 1


# --- Default Source Registry --------------------------------------------


def default_sources(artifacts_dir: Path) -> list[CompactionSource]:
    """Standard-3-Sources der MVP-Compaction. Reihenfolge ist relevant:
    audits müssen vor outcomes laufen (UPDATE braucht existierende rows).
    """
    return [
        CompactionSource(
            name="paper_execution_audit",
            jsonl_path=artifacts_dir / "paper_execution_audit.jsonl",
            handler=handle_paper_execution_row,
        ),
        CompactionSource(
            name="alert_audit",
            jsonl_path=artifacts_dir / "alert_audit.jsonl",
            handler=handle_alert_audit_row,
        ),
        CompactionSource(
            name="alert_outcomes",
            jsonl_path=artifacts_dir / "alert_outcomes.jsonl",
            handler=handle_alert_outcome_row,
        ),
    ]


# --- Watermark-Hilfen ---------------------------------------------------


def _read_watermark(
    con: duckdb.DuckDBPyConnection, source: str
) -> tuple[int, str | None, int]:
    """Returns (last_byte_offset, last_event_id, rows_processed)."""
    row = con.execute(
        "SELECT last_byte_offset, last_event_id, rows_processed "
        "FROM _compaction_watermark WHERE source = ?",
        [source],
    ).fetchone()
    if row is None:
        return 0, None, 0
    return int(row[0] or 0), (str(row[1]) if row[1] is not None else None), int(row[2] or 0)


def _write_watermark(
    con: duckdb.DuckDBPyConnection,
    *,
    source: str,
    last_byte_offset: int,
    last_event_id: str | None,
    rows_added: int,
    rows_skipped_dup: int,
    error: str | None,
) -> None:
    """INSERT-or-UPDATE _compaction_watermark.

    DuckDB unterstützt ON CONFLICT seit 0.9; wir nutzen die explicit
    UPSERT-Syntax. Counters werden additiv geupdatet, sodass die
    Lifetime-Summe sichtbar bleibt.
    """
    now = datetime.now(UTC)
    con.execute(
        """
        INSERT INTO _compaction_watermark(
            source, last_byte_offset, last_event_id, last_run_at,
            rows_processed, rows_skipped_dup, last_error
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (source) DO UPDATE SET
            last_byte_offset = excluded.last_byte_offset,
            last_event_id = excluded.last_event_id,
            last_run_at = excluded.last_run_at,
            rows_processed = _compaction_watermark.rows_processed + excluded.rows_processed,
            rows_skipped_dup = _compaction_watermark.rows_skipped_dup + excluded.rows_skipped_dup,
            last_error = excluded.last_error
        """,
        [
            source,
            last_byte_offset,
            last_event_id,
            now,
            rows_added,
            rows_skipped_dup,
            error,
        ],
    )


# --- Stream-Parse mit Byte-Tracking -------------------------------------


@dataclass
class _ParseStats:
    rows_read: int = 0
    rows_invalid: int = 0
    last_event_id: str | None = None
    safe_offset: int = 0  # offset BIS WO inklusive eine vollständige Line geparst wurde


def _stream_parse_from_offset(
    path: Path, *, start_offset: int
) -> tuple[list[dict[str, Any]], _ParseStats]:
    """Read JSONL ab byte-offset; gibt records + stats zurück.

    Halb-geschriebene letzte Zeile (kein \\n am Ende ODER JSONDecodeError
    nur auf der allerletzten Zeile) wird NICHT konsumiert — der
    safe_offset bleibt vor dieser Zeile. Beim nächsten Run wird sie
    erneut versucht.
    """
    if not path.exists():
        return [], _ParseStats()
    file_size = path.stat().st_size
    if start_offset >= file_size:
        return [], _ParseStats(safe_offset=start_offset)

    records: list[dict[str, Any]] = []
    stats = _ParseStats(safe_offset=start_offset)

    with path.open("rb") as f:
        f.seek(start_offset)
        # Read rest of file. For very-large catch-up backfills this is
        # streamed line by line; for the common steady-state case
        # (delta < few KB) memory pressure is negligible.
        while True:
            line_start = f.tell()
            raw = f.readline()
            if not raw:
                break
            # Detect partial last line: no terminating \n means writer was
            # mid-append. Don't consume it.
            if not raw.endswith(b"\n"):
                # safe_offset stays at line_start.
                break
            stripped = raw.strip()
            stats.rows_read += 1
            if not stripped:
                stats.safe_offset = f.tell()
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                # Mid-file corruption: log + skip but advance offset
                # (otherwise we'd loop on the same broken line forever).
                stats.rows_invalid += 1
                stats.safe_offset = f.tell()
                continue
            if not isinstance(obj, dict):
                stats.rows_invalid += 1
                stats.safe_offset = f.tell()
                continue
            records.append(obj)
            stats.safe_offset = f.tell()
            # Track event_id for audit anchor; multiple keys depending on source
            for key in ("fill_id", "document_id", "audit_id", "event_id"):
                if key in obj and isinstance(obj[key], str):
                    stats.last_event_id = obj[key]
                    break
    return records, stats


# --- Public Compactor ---------------------------------------------------


def compact_source(
    con: duckdb.DuckDBPyConnection,
    source: CompactionSource,
) -> CompactionResult:
    """Run inkrementelle Compaction für eine einzelne Source.

    Atomic: alle inserts + watermark-update in einer Transaction.
    Bei Exception: ROLLBACK, watermark bleibt unverändert.
    """
    last_offset, _last_id, _rows_done = _read_watermark(con, source.name)

    try:
        records, stats = _stream_parse_from_offset(source.jsonl_path, start_offset=last_offset)
    except OSError as exc:
        logger.warning("[compaction:%s] read error: %s", source.name, exc)
        return CompactionResult(
            source=source.name,
            rows_read=0,
            rows_applied=0,
            rows_invalid=0,
            bytes_advanced=0,
            last_event_id=None,
            error=str(exc)[:200],
        )

    rows_applied = 0
    try:
        con.execute("BEGIN TRANSACTION")
        for record in records:
            applied = source.handler(con, record)
            rows_applied += applied
        _write_watermark(
            con,
            source=source.name,
            last_byte_offset=stats.safe_offset,
            last_event_id=stats.last_event_id,
            rows_added=rows_applied,
            rows_skipped_dup=stats.rows_read - rows_applied - stats.rows_invalid,
            error=None,
        )
        con.execute("COMMIT")
    except Exception as exc:  # noqa: BLE001 — rollback + record error
        con.execute("ROLLBACK")
        logger.error("[compaction:%s] transaction failed: %s", source.name, exc)
        # Best-effort: persist the error visibility WITHOUT advancing offset
        try:
            con.execute("BEGIN TRANSACTION")
            _write_watermark(
                con,
                source=source.name,
                last_byte_offset=last_offset,  # unchanged
                last_event_id=None,
                rows_added=0,
                rows_skipped_dup=0,
                error=str(exc)[:200],
            )
            con.execute("COMMIT")
        except Exception:  # noqa: BLE001 — give up gracefully
            pass
        return CompactionResult(
            source=source.name,
            rows_read=stats.rows_read,
            rows_applied=0,
            rows_invalid=stats.rows_invalid,
            bytes_advanced=0,
            last_event_id=None,
            error=str(exc)[:200],
        )

    return CompactionResult(
        source=source.name,
        rows_read=stats.rows_read,
        rows_applied=rows_applied,
        rows_invalid=stats.rows_invalid,
        bytes_advanced=stats.safe_offset - last_offset,
        last_event_id=stats.last_event_id,
        error=None,
    )


def run_full_compaction(
    con: duckdb.DuckDBPyConnection,
    artifacts_dir: Path,
    *,
    sources: list[CompactionSource] | None = None,
) -> dict[str, CompactionResult]:
    """Run all sources sequentially. Returns per-source results."""
    src_list = sources if sources is not None else default_sources(artifacts_dir)
    results: dict[str, CompactionResult] = {}
    for src in src_list:
        result = compact_source(con, src)
        results[src.name] = result
        logger.info(
            "[compaction:%s] read=%d applied=%d invalid=%d bytes_advanced=%d error=%s",
            result.source,
            result.rows_read,
            result.rows_applied,
            result.rows_invalid,
            result.bytes_advanced,
            result.error or "-",
        )
    return results


# --- Helpers ------------------------------------------------------------


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False  # bool is int-subclass — exclude explicitly
    if isinstance(value, (int, float)):
        # NaN and inf would screw aggregations
        return value == value and value not in (float("inf"), float("-inf"))
    return False
