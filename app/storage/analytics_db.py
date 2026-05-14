"""DuckDB-backed analytical reads for KAI dashboard metrics."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import duckdb

logger = logging.getLogger(__name__)

_ARTIFACTS = Path("artifacts")
_ANALYTICS_DB = _ARTIFACTS / "analytics.duckdb"
_ALERT_AUDIT = _ARTIFACTS / "alert_audit.jsonl"
_ALERT_OUTCOMES = _ARTIFACTS / "alert_outcomes.jsonl"
_PAPER_EXECUTION_AUDIT = _ARTIFACTS / "paper_execution_audit.jsonl"
_TRADING_LOOP_AUDIT = _ARTIFACTS / "trading_loop_audit.jsonl"
_MAX_JSON_OBJECT_SIZE = 10_485_760


def get_connection() -> duckdb.DuckDBPyConnection:
    """Return a DuckDB connection, creating the local analytics file if needed."""
    _ARTIFACTS.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(_ANALYTICS_DB))


def _path_literal(path: Path) -> str:
    escaped = str(path).replace("\\", "/").replace("'", "''")
    return f"'{escaped}'"


def _table_exists(con: duckdb.DuckDBPyConnection, table_name: str) -> bool:
    rows = con.execute("SHOW TABLES").fetchall()
    return any(str(row[0]) == table_name for row in rows)


def _replace_table_from_jsonl(
    con: duckdb.DuckDBPyConnection,
    *,
    table_name: str,
    source_path: Path,
) -> None:
    if not source_path.exists():
        return
    # table_name is a hardcoded internal literal (execution_audit/alert_audit/
    # alert_outcomes/loop_audit, see callers below); never external input.
    con.execute(
        f"CREATE OR REPLACE TABLE {table_name} AS "  # nosec B608
        f"SELECT * FROM read_json_auto("
        f"{_path_literal(source_path)}, maximum_object_size={_MAX_JSON_OBJECT_SIZE}"
        ")"
    )


def run_compaction() -> None:
    """Compact JSONL audit artifacts into DuckDB tables for dashboard reads."""
    logger.info("Starting DuckDB compaction...")
    try:
        with get_connection() as con:
            _replace_table_from_jsonl(
                con,
                table_name="execution_audit",
                source_path=_PAPER_EXECUTION_AUDIT,
            )
            _replace_table_from_jsonl(
                con,
                table_name="alert_audit",
                source_path=_ALERT_AUDIT,
            )
            _replace_table_from_jsonl(
                con,
                table_name="alert_outcomes",
                source_path=_ALERT_OUTCOMES,
            )
            _replace_table_from_jsonl(
                con,
                table_name="loop_audit",
                source_path=_TRADING_LOOP_AUDIT,
            )
            logger.info("DuckDB compaction completed successfully.")
    except Exception as exc:  # noqa: BLE001
        logger.error("DuckDB compaction failed: %s", exc)


def get_attribution_pnl() -> dict[str, dict[str, float | int]]:
    """Return realized PnL grouped by paper execution source tag."""
    with get_connection() as con:
        try:
            if not _table_exists(con, "execution_audit"):
                return {}

            query = """
                WITH pnl_rows AS (
                    SELECT
                        COALESCE(NULLIF(TRIM(source_tag), ''), 'unknown') AS tag,
                        CASE
                            WHEN schema_version = 'v2' OR trade_pnl_usd IS NOT NULL
                                THEN COALESCE(trade_pnl_usd, 0.0)
                            WHEN position_side = 'short'
                                THEN -(COALESCE(exit_price, 0.0) - COALESCE(entry_price, 0.0))
                                    * COALESCE(quantity, 0.0)
                            ELSE (COALESCE(exit_price, 0.0) - COALESCE(entry_price, 0.0))
                                * COALESCE(quantity, 0.0)
                        END AS trade_pnl
                    FROM execution_audit
                    WHERE event_type IN ('position_closed', 'position_partial_closed')
                )
                SELECT
                    tag,
                    SUM(trade_pnl) AS total_pnl_usd,
                    COUNT(CASE WHEN trade_pnl > 0 THEN 1 END) AS win_count,
                    COUNT(CASE WHEN trade_pnl < 0 THEN 1 END) AS loss_count
                FROM pnl_rows
                GROUP BY tag
            """
            rows = con.execute(query).fetchall()
            result: dict[str, dict[str, float | int]] = {}
            for tag, total_pnl_usd, win_count, loss_count in rows:
                result[str(tag)] = {
                    "total_pnl_usd": round(float(total_pnl_usd or 0.0), 2),
                    "win_count": int(win_count or 0),
                    "loss_count": int(loss_count or 0),
                }
            return result
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to aggregate attribution PnL: %s", exc)
            return {}


def get_realized_pnl() -> tuple[float, int]:
    """Return total realized paper PnL and closed-position count."""
    with get_connection() as con:
        try:
            if not _table_exists(con, "execution_audit"):
                return 0.0, 0

            query = """
                WITH pnl_rows AS (
                    SELECT
                        CASE
                            WHEN schema_version = 'v2' OR trade_pnl_usd IS NOT NULL
                                THEN COALESCE(trade_pnl_usd, 0.0)
                            WHEN position_side = 'short'
                                THEN -(COALESCE(exit_price, 0.0) - COALESCE(entry_price, 0.0))
                                    * COALESCE(quantity, 0.0)
                            ELSE (COALESCE(exit_price, 0.0) - COALESCE(entry_price, 0.0))
                                * COALESCE(quantity, 0.0)
                        END AS trade_pnl
                    FROM execution_audit
                    WHERE event_type IN ('position_closed', 'position_partial_closed')
                )
                SELECT SUM(trade_pnl) AS total_pnl_usd, COUNT(*) AS positions_closed
                FROM pnl_rows
            """
            row = con.execute(query).fetchone()
            if row is None or row[0] is None:
                return 0.0, 0
            return round(float(row[0]), 2), int(row[1] or 0)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to calculate realized PnL: %s", exc)
            return 0.0, 0


def get_recent_alerts(limit: int = 20) -> list[dict[str, Any]]:
    """Return recent non-digest alerts, enriched with outcomes when available."""
    safe_limit = max(1, min(limit, 100))
    with get_connection() as con:
        try:
            if not _table_exists(con, "alert_audit"):
                return []

            if _table_exists(con, "alert_outcomes"):
                query = f"""
                    SELECT
                        a.document_id,
                        a.sentiment_label,
                        a.priority,
                        a.affected_assets,
                        a.dispatched_at,
                        COALESCE(o.outcome, '') AS outcome
                    FROM alert_audit a
                    LEFT JOIN alert_outcomes o ON a.document_id = o.document_id
                    WHERE a.is_digest IS NULL OR a.is_digest = false
                    ORDER BY a.dispatched_at DESC
                    LIMIT {safe_limit}
                """
            else:
                query = f"""
                    SELECT
                        document_id,
                        sentiment_label,
                        priority,
                        affected_assets,
                        dispatched_at,
                        '' AS outcome
                    FROM alert_audit
                    WHERE is_digest IS NULL OR is_digest = false
                    ORDER BY dispatched_at DESC
                    LIMIT {safe_limit}
                """

            rows = con.execute(query).fetchall()
            results: list[dict[str, Any]] = []
            for doc_id, sentiment, priority, assets, dispatched_at, outcome in rows:
                asset_list = assets if isinstance(assets, list) else []
                results.append(
                    {
                        "doc_id": str(doc_id or "")[:12],
                        "sentiment": str(sentiment or ""),
                        "priority": priority,
                        "assets": asset_list,
                        "dispatched_at": str(dispatched_at or "")[:16],
                        "outcome": str(outcome or ""),
                    }
                )
            return results
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to fetch recent alerts: %s", exc)
            return []


def get_loop_status_counts() -> dict[str, int]:
    """Return trading-loop status counts."""
    with get_connection() as con:
        try:
            if not _table_exists(con, "loop_audit"):
                return {}

            rows = con.execute("SELECT status, COUNT(*) FROM loop_audit GROUP BY status").fetchall()
            return {str(row[0] or "unknown"): int(row[1] or 0) for row in rows}
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to aggregate loop status counts: %s", exc)
            return {}


def get_paper_fills_count() -> int:
    """Return the number of recorded paper-fill events."""
    with get_connection() as con:
        try:
            if not _table_exists(con, "execution_audit"):
                return 0

            row = con.execute(
                "SELECT COUNT(*) FROM execution_audit WHERE event_type = 'order_filled'"
            ).fetchone()
            return int(row[0] or 0) if row else 0
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to get paper fills count: %s", exc)
            return 0
