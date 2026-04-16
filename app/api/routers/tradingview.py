"""TradingView webhook ingest — TV-1 scope (audit-only, fail-closed).

TV-1 contract:
    - Endpoint accepts HMAC-SHA256-signed alert payloads from TradingView.
    - Payloads are validated, deduplicated, persisted to an append-only JSONL
      audit log.
    - NO signal-pipeline wiring. TV-1 is deliberately audit-only; signal
      ingestion lands in TV-3 behind explicit provenance tagging.

Security invariants:
    - Router is mounted only when TRADINGVIEW_WEBHOOK_ENABLED=true AND a
      non-empty TRADINGVIEW_WEBHOOK_SECRET is configured. Any other state
      returns 404 to keep the endpoint invisible.
    - Signature verification is constant-time (hmac.compare_digest).
    - Body-size cap is enforced upstream by RequestGovernanceMiddleware
      (APP_MAX_REQUEST_BODY_BYTES, default 64 KiB).
    - Duplicate payloads (same hash within replay window) are rejected.
    - Every request — accepted, rejected, replayed — is recorded in the
      audit log with an outcome tag. No raw secrets are logged.

See: docs/adr/0001-tradingview-integration.md
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from app.core.logging import get_logger
from app.core.settings import AppSettings, TradingViewSettings, get_settings

_logger = get_logger(__name__)

_SIGNATURE_HEADER = "X-KAI-Signature"
_SIGNATURE_PREFIX = "sha256="


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def _new_request_id() -> str:
    return f"tvwh_{uuid4().hex[:16]}"


class ReplayCache:
    """LRU cache of recently-seen payload hashes for replay protection.

    WARNING: Single-instance. A multi-process deployment must replace this
    with a shared backend before horizontal scaling.
    """

    def __init__(self, max_size: int, window_seconds: float) -> None:
        self._max_size = max_size
        self._window_seconds = window_seconds
        self._lock = Lock()
        self._seen: OrderedDict[str, float] = OrderedDict()

    def check_and_record(self, payload_hash: str) -> bool:
        """Return True if payload is new (accepted), False if replay."""
        now = time.monotonic()
        with self._lock:
            cutoff = now - self._window_seconds
            while self._seen and next(iter(self._seen.values())) < cutoff:
                self._seen.popitem(last=False)
            if payload_hash in self._seen:
                return False
            self._seen[payload_hash] = now
            if len(self._seen) > self._max_size:
                self._seen.popitem(last=False)
            return True

    def clear(self) -> None:
        with self._lock:
            self._seen.clear()


_REPLAY_CACHE: ReplayCache | None = None


def _get_replay_cache(settings: TradingViewSettings) -> ReplayCache:
    global _REPLAY_CACHE
    if _REPLAY_CACHE is None:
        _REPLAY_CACHE = ReplayCache(
            max_size=settings.webhook_replay_cache_size,
            window_seconds=settings.webhook_replay_window_seconds,
        )
    return _REPLAY_CACHE


def _reset_replay_cache_for_tests() -> None:
    """Test hook — clear singleton so each test starts clean."""
    global _REPLAY_CACHE
    _REPLAY_CACHE = None


def _verify_signature(raw_body: bytes, signature_header: str | None, secret: str) -> bool:
    if not signature_header or not secret:
        return False
    if not signature_header.startswith(_SIGNATURE_PREFIX):
        return False
    provided = signature_header[len(_SIGNATURE_PREFIX):].strip()
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(provided, expected)


def _payload_hash(raw_body: bytes) -> str:
    return hashlib.sha256(raw_body).hexdigest()


def _append_audit(audit_path: Path, entry: dict[str, object]) -> None:
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")


router = APIRouter(prefix="/tradingview", tags=["tradingview"])


AuditWriter = Callable[[Path, dict[str, object]], None]
"""Injection hook for the append function — tests replace this with a no-op."""


def _default_audit_writer(path: Path, entry: dict[str, object]) -> None:
    _append_audit(path, entry)


_audit_writer: AuditWriter = _default_audit_writer


def set_audit_writer(writer: AuditWriter) -> None:
    """Replace the audit-writer (test-only hook)."""
    global _audit_writer
    _audit_writer = writer


def reset_audit_writer() -> None:
    global _audit_writer
    _audit_writer = _default_audit_writer


def _settings_gate(settings: AppSettings) -> TradingViewSettings:
    """Return TV-settings if endpoint is fully configured; otherwise 404.

    Fail-closed: an unconfigured or disabled webhook is indistinguishable
    from a non-existent endpoint.
    """
    tv = settings.tradingview
    if not tv.webhook_enabled or not tv.webhook_secret:
        raise HTTPException(status_code=404, detail="Not Found")
    return tv


async def _require_tradingview_settings(
    settings: Annotated[AppSettings, Depends(get_settings)],
) -> TradingViewSettings:
    return _settings_gate(settings)


@router.post("/webhook", status_code=202)
async def tradingview_webhook(
    request: Request,
    tv: Annotated[TradingViewSettings, Depends(_require_tradingview_settings)],
    x_kai_signature: Annotated[str | None, Header(alias=_SIGNATURE_HEADER)] = None,
) -> dict[str, object]:
    """Accept a signed TradingView alert payload.

    Returns 202 Accepted for valid, new payloads.
    401 for bad signatures. 409 for replayed payloads. 400 for malformed JSON.
    """
    request_id = _new_request_id()
    audit_path = Path(tv.webhook_audit_log)
    raw_body = await request.body()

    common_log: dict[str, object] = {
        "request_id": request_id,
        "received_at": _utcnow_iso(),
        "source_ip": request.client.host if request.client else None,
        "body_bytes": len(raw_body),
    }

    if not _verify_signature(raw_body, x_kai_signature, tv.webhook_secret):
        entry = {**common_log, "outcome": "rejected", "reason": "invalid_signature"}
        _audit_writer(audit_path, entry)
        _logger.warning("tradingview_webhook_rejected", **entry)
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload_hash = _payload_hash(raw_body)
    cache = _get_replay_cache(tv)
    if not cache.check_and_record(payload_hash):
        entry = {
            **common_log,
            "outcome": "rejected",
            "reason": "replay",
            "payload_hash": payload_hash,
        }
        _audit_writer(audit_path, entry)
        _logger.info("tradingview_webhook_replay", **entry)
        raise HTTPException(status_code=409, detail="Replay detected")

    try:
        parsed_payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        entry = {
            **common_log,
            "outcome": "rejected",
            "reason": "malformed_json",
            "error": str(exc)[:200],
            "payload_hash": payload_hash,
        }
        _audit_writer(audit_path, entry)
        _logger.warning("tradingview_webhook_malformed", **entry)
        raise HTTPException(status_code=400, detail="Malformed payload") from None

    entry = {
        **common_log,
        "outcome": "accepted",
        "payload_hash": payload_hash,
        "payload": parsed_payload,
        "provenance": {
            "source": "tradingview_webhook",
            "version": "tv-1",
            "signal_path_id": None,  # TV-1: audit only, no pipeline routing
        },
    }
    _audit_writer(audit_path, entry)
    _logger.info(
        "tradingview_webhook_accepted",
        request_id=request_id,
        payload_hash=payload_hash,
        body_bytes=len(raw_body),
    )
    return {
        "status": "accepted",
        "request_id": request_id,
        "received_at": entry["received_at"],
    }
