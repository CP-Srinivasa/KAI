"""SQLite event store for the premium-signal pipeline.

JSONL remains the append-only audit trail. This module adds a small
operational store with unique constraints, so replay/cutover code has a
durable source identity to converge on.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_EVENT_STORE_PATH = Path("artifacts/premium_signal_events.sqlite3")


def event_store_enabled() -> bool:
    raw = os.environ.get("KAI_PREMIUM_EVENT_STORE_ENABLED", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def event_store_path() -> Path:
    raw = os.environ.get("KAI_PREMIUM_EVENT_STORE_PATH", "").strip()
    return Path(raw) if raw else DEFAULT_EVENT_STORE_PATH


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _payload(record: dict[str, Any]) -> dict[str, Any]:
    payload = record.get("payload")
    return payload if isinstance(payload, dict) else {}


def _source_uid(record: dict[str, Any]) -> str | None:
    raw = record.get("source_uid")
    if isinstance(raw, str) and raw:
        return raw
    payload = _payload(record)
    raw = payload.get("source_uid")
    return raw if isinstance(raw, str) and raw else None


def _source_platform(record: dict[str, Any]) -> str | None:
    raw = record.get("source_platform")
    if isinstance(raw, str) and raw:
        return raw
    payload = _payload(record)
    raw = payload.get("source_platform")
    return raw if isinstance(raw, str) and raw else None


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


@contextmanager
def connect(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    target = path or event_store_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        ensure_schema(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS signals (
            source_uid TEXT PRIMARY KEY,
            source_platform TEXT NOT NULL,
            chat_id INTEGER,
            message_id INTEGER,
            symbol TEXT,
            direction TEXT,
            received_at TEXT,
            raw_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS envelopes (
            envelope_id TEXT PRIMARY KEY,
            source_uid TEXT,
            idempotency_key TEXT UNIQUE,
            source TEXT,
            stage TEXT,
            status TEXT,
            timestamp_utc TEXT,
            payload_json TEXT NOT NULL,
            raw_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(source_uid) REFERENCES signals(source_uid)
        );

        CREATE TABLE IF NOT EXISTS approvals (
            envelope_id TEXT PRIMARY KEY,
            origin_envelope_id TEXT NOT NULL,
            source_uid TEXT,
            status TEXT,
            approved_by TEXT,
            timestamp_utc TEXT,
            raw_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS bridge_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            envelope_id TEXT NOT NULL,
            correlation_id TEXT,
            source_uid TEXT,
            stage TEXT NOT NULL,
            reason TEXT,
            timestamp_utc TEXT,
            raw_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(envelope_id, stage, timestamp_utc)
        );

        CREATE TABLE IF NOT EXISTS orders (
            order_id TEXT PRIMARY KEY,
            envelope_id TEXT,
            source_uid TEXT,
            symbol TEXT,
            side TEXT,
            quantity REAL,
            status TEXT,
            timestamp_utc TEXT,
            raw_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS fills (
            fill_id TEXT PRIMARY KEY,
            order_id TEXT,
            envelope_id TEXT,
            source_uid TEXT,
            symbol TEXT,
            side TEXT,
            quantity REAL,
            price REAL,
            realized_pnl_usd REAL,
            timestamp_utc TEXT,
            raw_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )


def record_envelope(record: dict[str, Any], *, path: Path | None = None) -> None:
    if not event_store_enabled():
        return
    try:
        with connect(path) as conn:
            _record_envelope(conn, record)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[premium-event-store] envelope write failed: %s", exc)


def record_approval(record: dict[str, Any], *, path: Path | None = None) -> None:
    if not event_store_enabled():
        return
    try:
        with connect(path) as conn:
            _record_approval(conn, record)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[premium-event-store] approval write failed: %s", exc)


def record_bridge_decision(record: dict[str, Any], *, path: Path | None = None) -> None:
    if not event_store_enabled():
        return
    try:
        with connect(path) as conn:
            _record_bridge_decision(conn, record)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[premium-event-store] bridge write failed: %s", exc)


def source_uid_exists(source_uid: str, *, path: Path | None = None) -> bool:
    if not event_store_enabled() or not source_uid:
        return False
    try:
        with connect(path) as conn:
            row = conn.execute(
                "SELECT 1 FROM signals WHERE source_uid = ? LIMIT 1",
                (source_uid,),
            ).fetchone()
            return row is not None
    except Exception as exc:  # noqa: BLE001
        logger.warning("[premium-event-store] source_uid lookup failed: %s", exc)
        return False


def _record_signal_if_present(conn: sqlite3.Connection, record: dict[str, Any]) -> None:
    source_uid = _source_uid(record)
    if source_uid is None:
        return
    payload = _payload(record)
    now = datetime.now(UTC).isoformat()
    conn.execute(
        """
        INSERT OR IGNORE INTO signals (
            source_uid, source_platform, chat_id, message_id, symbol,
            direction, received_at, raw_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_uid,
            _source_platform(record) or "unknown",
            _coerce_int(record.get("chat_id") or payload.get("source_chat_id")),
            _coerce_int(record.get("message_id") or payload.get("source_message_id")),
            payload.get("display_symbol") or payload.get("symbol"),
            payload.get("direction"),
            record.get("timestamp_utc") or payload.get("timestamp_utc"),
            _json(record),
            now,
        ),
    )


def _record_envelope(conn: sqlite3.Connection, record: dict[str, Any]) -> None:
    env_id = record.get("envelope_id")
    if not isinstance(env_id, str) or not env_id:
        return
    _record_signal_if_present(conn, record)
    payload = _payload(record)
    now = datetime.now(UTC).isoformat()
    conn.execute(
        """
        INSERT OR IGNORE INTO envelopes (
            envelope_id, source_uid, idempotency_key, source, stage, status,
            timestamp_utc, payload_json, raw_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            env_id,
            _source_uid(record),
            record.get("idempotency_key"),
            record.get("source"),
            record.get("stage"),
            record.get("status"),
            record.get("timestamp_utc"),
            _json(payload),
            _json(record),
            now,
        ),
    )


def _record_approval(conn: sqlite3.Connection, record: dict[str, Any]) -> None:
    _record_envelope(conn, record)
    env_id = record.get("envelope_id")
    origin = record.get("origin_envelope_id")
    if not isinstance(env_id, str) or not env_id or not isinstance(origin, str):
        return
    now = datetime.now(UTC).isoformat()
    conn.execute(
        """
        INSERT OR REPLACE INTO approvals (
            envelope_id, origin_envelope_id, source_uid, status,
            approved_by, timestamp_utc, raw_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            env_id,
            origin,
            _source_uid(record),
            record.get("status"),
            str(record.get("approved_by", "")),
            record.get("timestamp_utc"),
            _json(record),
            now,
        ),
    )


def _record_bridge_decision(conn: sqlite3.Connection, record: dict[str, Any]) -> None:
    env_id = record.get("envelope_id")
    stage = record.get("stage")
    if not isinstance(env_id, str) or not env_id or not isinstance(stage, str):
        return
    now = datetime.now(UTC).isoformat()
    source_uid = _source_uid(record)
    conn.execute(
        """
        INSERT OR IGNORE INTO bridge_decisions (
            envelope_id, correlation_id, source_uid, stage, reason,
            timestamp_utc, raw_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            env_id,
            record.get("correlation_id"),
            source_uid,
            stage,
            record.get("reason") or record.get("audit_reason"),
            record.get("timestamp_utc"),
            _json(record),
            now,
        ),
    )
    order_id = record.get("order_id")
    if isinstance(order_id, str) and order_id:
        conn.execute(
            """
            INSERT OR REPLACE INTO orders (
                order_id, envelope_id, source_uid, symbol, side, quantity,
                status, timestamp_utc, raw_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                env_id,
                source_uid,
                record.get("symbol"),
                record.get("side"),
                record.get("quantity"),
                stage,
                record.get("timestamp_utc"),
                _json(record),
                now,
            ),
        )
    fill_id = record.get("fill_id")
    if isinstance(fill_id, str) and fill_id:
        conn.execute(
            """
            INSERT OR REPLACE INTO fills (
                fill_id, order_id, envelope_id, source_uid, symbol, side,
                quantity, price, realized_pnl_usd, timestamp_utc, raw_json,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fill_id,
                order_id,
                env_id,
                source_uid,
                record.get("symbol"),
                record.get("side"),
                record.get("quantity"),
                record.get("fill_price"),
                record.get("trade_pnl_usd") or record.get("realized_pnl_usd"),
                record.get("timestamp_utc"),
                _json(record),
                now,
            ),
        )


__all__ = [
    "DEFAULT_EVENT_STORE_PATH",
    "connect",
    "ensure_schema",
    "event_store_enabled",
    "event_store_path",
    "record_approval",
    "record_bridge_decision",
    "record_envelope",
    "source_uid_exists",
]
