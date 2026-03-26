"""Telegram signal exchange relay worker.

Consumes queued signal-forward records from JSONL outbox and forwards them to
an external HTTP endpoint. Uses bounded retry and dead-letter fallback.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import httpx

_QUEUE_EVENT = "telegram_signal_exchange_forward_queued"
_SENT_EVENT = "telegram_signal_exchange_forward_sent"
_DEAD_EVENT = "telegram_signal_exchange_forward_dead_letter"


@dataclass(frozen=True)
class RelayStats:
    processed: int
    sent: int
    requeued: int
    dead_lettered: int
    skipped: int

    def to_json_dict(self) -> dict[str, object]:
        return {
            "processed": self.processed,
            "sent": self.sent,
            "requeued": self.requeued,
            "dead_lettered": self.dead_lettered,
            "skipped": self.skipped,
            "execution_enabled": False,
            "write_back_allowed": False,
        }


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def _write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        delete=False,
        dir=str(path.parent),
        prefix=f".{path.name}.tmp.",
    ) as tmp:
        tmp_path = Path(tmp.name)
        for row in rows:
            tmp.write(json.dumps(row))
            tmp.write("\n")
    tmp_path.replace(path)


def _parse_iso(ts: object) -> datetime | None:
    if not isinstance(ts, str) or not ts.strip():
        return None
    candidate = ts.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


async def _post_signal(
    *,
    endpoint: str,
    api_key: str,
    timeout_seconds: int,
    payload: dict[str, Any],
) -> tuple[bool, int | None, str | None]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            resp = await client.post(endpoint, headers=headers, json=payload)
    except Exception as exc:  # noqa: BLE001
        return False, None, f"network_error:{exc.__class__.__name__}"
    if 200 <= resp.status_code < 300:
        return True, resp.status_code, None
    return False, resp.status_code, f"http_{resp.status_code}"


async def relay_exchange_outbox_once(
    *,
    outbox_path: str | Path,
    sent_log_path: str | Path,
    dead_letter_log_path: str | Path,
    endpoint: str,
    api_key: str = "",
    timeout_seconds: int = 10,
    max_attempts: int = 3,
    batch_size: int = 100,
) -> RelayStats:
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")

    outbox = Path(outbox_path)
    sent_log = Path(sent_log_path)
    dead_log = Path(dead_letter_log_path)

    rows = _load_jsonl(outbox)
    remaining: list[dict[str, Any]] = []
    normalized_endpoint = endpoint.strip()
    processed = 0
    sent = 0
    requeued = 0
    dead_lettered = 0
    skipped = 0

    for row in rows:
        event = str(row.get("event", ""))
        status = str(row.get("status", ""))
        if event != _QUEUE_EVENT or status != "queued":
            remaining.append(row)
            skipped += 1
            continue

        if processed >= batch_size:
            remaining.append(row)
            skipped += 1
            continue

        processed += 1
        attempts = int(row.get("attempt_count", 0) or 0)
        relay_payload = {
            "signal_id": row.get("signal_id"),
            "asset": row.get("asset"),
            "symbol": row.get("symbol"),
            "direction": row.get("direction"),
            "reasoning": row.get("reasoning"),
            "source": row.get("source"),
            "timestamp_utc": row.get("timestamp_utc"),
            "execution_enabled": False,
            "write_back_allowed": False,
        }

        error: str | None
        if not normalized_endpoint:
            error = "endpoint_not_configured"
            ok = False
            http_status = None
        else:
            ok, http_status, error = await _post_signal(
                endpoint=normalized_endpoint,
                api_key=api_key.strip(),
                timeout_seconds=timeout_seconds,
                payload=relay_payload,
            )

        next_attempt = attempts + 1
        if ok:
            sent += 1
            sent_row = dict(row)
            sent_row.update(
                {
                    "event": _SENT_EVENT,
                    "status": "sent",
                    "attempt_count": next_attempt,
                    "relayed_at_utc": _now_iso(),
                    "relay_endpoint": normalized_endpoint,
                    "relay_http_status": http_status,
                    "execution_enabled": False,
                    "write_back_allowed": False,
                }
            )
            _append_jsonl(sent_log, sent_row)
            continue

        if next_attempt >= max_attempts:
            dead_lettered += 1
            dead_row = dict(row)
            dead_row.update(
                {
                    "event": _DEAD_EVENT,
                    "status": "dead_letter",
                    "attempt_count": next_attempt,
                    "last_error": error or "unknown_error",
                    "last_http_status": http_status,
                    "dead_lettered_at_utc": _now_iso(),
                    "relay_endpoint": normalized_endpoint,
                    "execution_enabled": False,
                    "write_back_allowed": False,
                }
            )
            _append_jsonl(dead_log, dead_row)
            continue

        requeued += 1
        requeue_row = dict(row)
        requeue_row.update(
            {
                "status": "queued",
                "attempt_count": next_attempt,
                "last_error": error or "unknown_error",
                "last_http_status": http_status,
                "last_attempted_at_utc": _now_iso(),
                "execution_enabled": False,
                "write_back_allowed": False,
            }
        )
        remaining.append(requeue_row)

    _write_jsonl_atomic(outbox, remaining)
    return RelayStats(
        processed=processed,
        sent=sent,
        requeued=requeued,
        dead_lettered=dead_lettered,
        skipped=skipped,
    )


def build_signal_pipeline_status(
    *,
    handoff_log_path: str | Path,
    outbox_log_path: str | Path,
    sent_log_path: str | Path,
    dead_letter_log_path: str | Path,
    lookback_hours: int = 24,
) -> dict[str, object]:
    cutoff = datetime.now(UTC) - timedelta(hours=max(1, lookback_hours))
    handoff_rows = _load_jsonl(Path(handoff_log_path))
    outbox_rows = _load_jsonl(Path(outbox_log_path))
    sent_rows = _load_jsonl(Path(sent_log_path))
    dead_rows = _load_jsonl(Path(dead_letter_log_path))

    queued = [
        row
        for row in outbox_rows
        if str(row.get("event", "")) == _QUEUE_EVENT and str(row.get("status", "")) == "queued"
    ]

    def _count_since(rows: list[dict[str, Any]], ts_key: str) -> int:
        count = 0
        for row in rows:
            ts = _parse_iso(row.get(ts_key))
            if ts is not None and ts >= cutoff:
                count += 1
        return count

    return {
        "report_type": "telegram_signal_pipeline_status",
        "lookback_hours": max(1, lookback_hours),
        "handoff_total": len(handoff_rows),
        "handoff_lookback": _count_since(handoff_rows, "timestamp_utc"),
        "outbox_queued_total": len(queued),
        "exchange_sent_total": len(sent_rows),
        "exchange_sent_lookback": _count_since(sent_rows, "relayed_at_utc"),
        "exchange_dead_letter_total": len(dead_rows),
        "exchange_dead_letter_lookback": _count_since(dead_rows, "dead_lettered_at_utc"),
        "execution_enabled": False,
        "write_back_allowed": False,
    }
