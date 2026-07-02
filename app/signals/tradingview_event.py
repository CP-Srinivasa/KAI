"""TradingView signal event — TV-3 lightweight representation.

A `TradingViewSignalEvent` is the normalized form of a TradingView alert
payload. It carries only what a TV webhook actually contains
(ticker, action, price, optional note/strategy) plus a `SignalProvenance`
tag so downstream phases can attribute it correctly.

TV-3 scope:
    - Webhook payload -> TradingViewSignalEvent -> pending-signals JSONL.
    - NO promotion to `SignalCandidate`. NO auto-execution.
    - Operator approval (TV-4+) promotes pending events to full candidates.

This separation keeps the richer `SignalCandidate` contract (KAI decision
schema: thesis, confluence, risk assessment) from being filled with
synthetic defaults that TV alerts cannot honestly provide.

See: docs/adr/0001-tradingview-integration.md
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from app.signals.models import SignalProvenance

# SENTR-F-004: key under which the row-HMAC is stored inside each JSONL row.
# Leading underscore keeps it visually grouped with metadata (not a payload
# field) while remaining a valid JSON key.
TV_ROW_HMAC_FIELD = "_sig"

TradingViewAction = Literal["buy", "sell", "close", "unknown"]
_VALID_ACTIONS: set[str] = {"buy", "sell", "close"}

_TV_SOURCE = "tradingview_webhook"
_TV_VERSION = "tv-3"


def _new_event_id() -> str:
    return f"tvsig_{uuid4().hex[:16]}"


def _new_signal_path_id() -> str:
    return f"tvpath_{uuid4().hex[:12]}"


@dataclass(frozen=True)
class TradingViewSignalEvent:
    """Normalized TradingView alert event — pending until operator approval."""

    event_id: str
    received_at: str  # ISO UTC, mirrored from webhook audit entry
    ticker: str  # as sent by TV alert, trimmed; no normalization attempted here
    action: TradingViewAction
    price: float | None
    note: str | None
    strategy: str | None
    source_request_id: str  # links back to tradingview_webhook_audit.jsonl
    source_payload_hash: str
    external_event_id: str | None  # V8.1: operator-provided id from TV alert body
    provenance: SignalProvenance


class NormalizationError(ValueError):
    """Raised when a TradingView payload cannot be turned into an event."""


def _coerce_action(raw: Any) -> TradingViewAction:
    if not isinstance(raw, str):
        raise NormalizationError("action must be a string")
    value = raw.strip().lower()
    if value in _VALID_ACTIONS:
        return value  # type: ignore[return-value]
    raise NormalizationError(f"unsupported action: {value!r}")


def _coerce_ticker(raw: Any) -> str:
    if not isinstance(raw, str):
        raise NormalizationError("ticker must be a string")
    value = raw.strip()
    if not value:
        raise NormalizationError("ticker is empty")
    if len(value) > 64:
        raise NormalizationError("ticker too long")
    return value


def _coerce_price(raw: Any) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, bool):  # bool is subclass of int — reject explicitly
        raise NormalizationError("price must be numeric, not bool")
    if isinstance(raw, (int, float)):
        value = float(raw)
    elif isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            return None
        try:
            value = float(stripped)
        except ValueError as exc:
            raise NormalizationError(f"price not numeric: {raw!r}") from exc
    else:
        raise NormalizationError(f"price has unsupported type {type(raw).__name__}")
    if value <= 0:
        raise NormalizationError("price must be positive")
    return value


def _coerce_optional_str(raw: Any, *, max_len: int = 1024) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        return None  # silently drop non-string metadata rather than reject
    value = raw.strip()
    if not value:
        return None
    if len(value) > max_len:
        return value[:max_len]
    return value


def extract_external_event_id(payload: dict[str, Any]) -> str | None:
    """Return the operator-supplied ``event_id`` from a parsed TV payload, if any.

    Shared by the webhook router (Layer-2 replay guard) and the normalizer
    (event-object attribution). Empty / non-string / oversized values are
    treated as absent — pass-through rather than poison the cache key.
    """
    if not isinstance(payload, dict):
        return None
    raw = payload.get("event_id")
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    if not value:
        return None
    if len(value) > 128:
        return value[:128]
    return value


def normalize_tradingview_payload(
    payload: dict[str, Any],
    *,
    request_id: str,
    payload_hash: str,
    received_at: str,
    external_event_id: str | None = None,
    auth_method: str | None = None,
) -> TradingViewSignalEvent:
    """Turn a parsed TV alert JSON into a TradingViewSignalEvent.

    Raises NormalizationError on missing/invalid required fields
    (`ticker`, `action`). Optional fields silently absent are allowed.

    ``external_event_id`` is injected by the router after its own extraction
    (so Layer-2 dedup and the attached event-object agree on the same value).
    If omitted, the normalizer re-extracts it from the payload itself so
    direct callers still get the attribution.

    ``auth_method`` is the ingress credential mode used by the webhook router
    (``"hmac"`` or ``"shared_token"``). The normalizer is also called from
    operator-replay paths where no auth happened — those leave the field
    ``None`` and the provenance reader treats it as unknown.
    """
    if not isinstance(payload, dict):
        raise NormalizationError("payload must be a JSON object")

    ticker = _coerce_ticker(payload.get("ticker"))
    action = _coerce_action(payload.get("action"))
    price = _coerce_price(payload.get("price"))
    note = _coerce_optional_str(payload.get("note"))
    strategy = _coerce_optional_str(payload.get("strategy"), max_len=128)
    resolved_event_id = (
        external_event_id if external_event_id is not None else extract_external_event_id(payload)
    )

    provenance = SignalProvenance(
        source=_TV_SOURCE,
        version=_TV_VERSION,
        signal_path_id=_new_signal_path_id(),
        auth_method=auth_method,
    )
    return TradingViewSignalEvent(
        event_id=_new_event_id(),
        received_at=received_at,
        ticker=ticker,
        action=action,
        price=price,
        note=note,
        strategy=strategy,
        source_request_id=request_id,
        source_payload_hash=payload_hash,
        external_event_id=resolved_event_id,
        provenance=provenance,
    )


def event_to_jsonl_dict(event: TradingViewSignalEvent) -> dict[str, Any]:
    """Serialize event for JSONL — dataclass asdict flattens provenance too."""
    return asdict(event)


def _canonical_payload_bytes(payload: dict[str, Any]) -> bytes:
    """Canonical JSON for HMAC input — sorted keys, no whitespace, no HMAC field.

    The signature is computed over the payload *without* its own ``_sig``
    field so verification is symmetric: stripping the field and re-signing
    must reproduce the stored digest.
    """
    filtered = {k: v for k, v in payload.items() if k != TV_ROW_HMAC_FIELD}
    return json.dumps(filtered, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )


def compute_row_hmac(payload: dict[str, Any], secret: str) -> str:
    """Return hex HMAC-SHA256 over the canonical payload."""
    return hmac.new(
        secret.encode("utf-8"),
        _canonical_payload_bytes(payload),
        hashlib.sha256,
    ).hexdigest()


def verify_row_hmac(payload: dict[str, Any], secret: str) -> bool:
    """Constant-time verify the ``_sig`` field against the recomputed HMAC."""
    stored = payload.get(TV_ROW_HMAC_FIELD)
    if not isinstance(stored, str) or not stored:
        return False
    expected = compute_row_hmac(payload, secret)
    return hmac.compare_digest(stored, expected)


def append_pending_signal(
    path: Path, event: TradingViewSignalEvent, *, hmac_secret: str = ""
) -> None:
    """Append one event to the pending-signals JSONL (append-only).

    When ``hmac_secret`` is non-empty (SENTR-F-004), an HMAC-SHA256 of the
    row's canonical JSON is written into the ``_sig`` field. The reader
    (tv_bridge) verifies this signature and skips tampered or unsigned
    rows when the same secret is configured on its side.
    """
    payload = event_to_jsonl_dict(event)
    if hmac_secret:
        payload[TV_ROW_HMAC_FIELD] = compute_row_hmac(payload, hmac_secret)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
