"""TV-4 preparation: measurement-only consumer of promoted TV signals.

The consumer scans `tradingview_promoted_signals.jsonl` and records each
promoted `decision_id` exactly once in an own append-only audit stream
(`tradingview_signal_audit.jsonl`). It has **no trading-loop side
effects**: it does not execute, paper-trade, or mutate any other KAI
state. Its sole purpose is to provide the future TV-4 precision/hold
surface with an authoritative "signal was consumed at T" trail so the
quality-bar phase can join against price outcomes.

Invariants
----------
* Fail-closed: disabled by default (``TRADINGVIEW_PROMOTED_CONSUMER_ENABLED=false``).
  A disabled consumer performs no reads and no writes.
* Append-only: audit rows are only appended, never rewritten. The file
  is the source of truth for which ``decision_id``s have been consumed.
* Idempotent: replaying the consumer on the same promoted log is a
  no-op after the first run. Identity key is ``decision_id``.
* Malformed rows in either file are skipped silently (consistent with
  ``tradingview_promotion.load_pending_events``).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.core.settings import TradingViewSettings


@dataclass(frozen=True)
class ConsumedSignal:
    """One row in the signal-audit stream.

    Mirrors only the promoted-candidate fields needed for downstream
    precision measurement. The full candidate remains in
    ``tradingview_promoted_signals.jsonl``; this stream is a join key.
    """

    decision_id: str
    consumed_at: str
    symbol: str
    direction: str
    entry_price: float
    confidence_score: float
    source_event_id: str
    signal_path_id: str | None


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _already_consumed(audit_path: Path) -> set[str]:
    if not audit_path.exists():
        return set()
    seen: set[str] = set()
    for line in audit_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            decision_id = raw.get("decision_id")
        except (json.JSONDecodeError, AttributeError):
            continue
        if isinstance(decision_id, str) and decision_id:
            seen.add(decision_id)
    return seen


def _load_promoted_rows(promoted_path: Path) -> list[dict[str, object]]:
    if not promoted_path.exists():
        return []
    rows: list[dict[str, object]] = []
    for line in promoted_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(raw, dict):
            rows.append(raw)
    return rows


def _to_consumed(row: dict[str, object], *, now_iso: str) -> ConsumedSignal | None:
    decision_id = row.get("decision_id")
    symbol = row.get("symbol")
    direction = row.get("direction")
    entry_price = row.get("entry_price")
    confidence = row.get("confidence_score")
    source_event_id = row.get("source_document_id", "")
    provenance = row.get("provenance") if isinstance(row.get("provenance"), dict) else {}
    signal_path_id = (
        provenance.get("signal_path_id") if isinstance(provenance, dict) else None
    )
    if not isinstance(decision_id, str) or not decision_id:
        return None
    if not isinstance(symbol, str) or not isinstance(direction, str):
        return None
    try:
        entry_value = float(entry_price)  # type: ignore[arg-type]
        confidence_value = float(confidence)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return ConsumedSignal(
        decision_id=decision_id,
        consumed_at=now_iso,
        symbol=symbol,
        direction=direction,
        entry_price=entry_value,
        confidence_score=confidence_value,
        source_event_id=source_event_id if isinstance(source_event_id, str) else "",
        signal_path_id=signal_path_id if isinstance(signal_path_id, str) else None,
    )


def _append(audit_path: Path, entry: ConsumedSignal) -> None:
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(asdict(entry), ensure_ascii=False, separators=(",", ":")) + "\n")


def consume_promoted_signals(
    settings: TradingViewSettings,
    *,
    now_iso: str | None = None,
) -> list[ConsumedSignal]:
    """Consume new promoted rows; return what was freshly appended.

    Fail-closed: when the consumer is disabled the function returns an
    empty list without touching any files. Enabled, it appends one audit
    row per previously unseen ``decision_id`` and returns those rows.
    """
    if not settings.promoted_consumer_enabled:
        return []
    promoted_path = Path(settings.promoted_signals_log)
    audit_path = Path(settings.promoted_signal_audit_log)
    seen = _already_consumed(audit_path)
    timestamp = now_iso or _utc_now_iso()
    newly: list[ConsumedSignal] = []
    for row in _load_promoted_rows(promoted_path):
        entry = _to_consumed(row, now_iso=timestamp)
        if entry is None or entry.decision_id in seen:
            continue
        _append(audit_path, entry)
        seen.add(entry.decision_id)
        newly.append(entry)
    return newly
