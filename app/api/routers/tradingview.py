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
import sqlite3
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
from app.security.rate_limit import FailureTracker, client_ip
from app.signals.tradingview_event import (
    NormalizationError,
    TradingViewSignalEvent,
    append_pending_signal,
    extract_external_event_id,
    normalize_tradingview_payload,
)

_logger = get_logger(__name__)

_SIGNATURE_HEADER = "X-KAI-Signature"
_SIGNATURE_PREFIX = "sha256="
_TOKEN_HEADER = "X-KAI-Token"


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


class PersistentReplayCache(ReplayCache):
    """SQLite-backed replay cache that survives uvicorn/systemd restarts.

    D-189 / NEO-F-META-20260424-026: without persistence, a restart within the
    replay window reopens the replay door — attacker with a stolen signed body
    (or event_id) could re-submit until the natural time window expires. This
    subclass mirrors every accepted ``check_and_record`` into a single-file
    SQLite store; on construction it rehydrates within-window rows back into
    the in-memory OrderedDict, so the guard stays closed across restarts.

    The in-memory path is unchanged (same monotonic-based LRU). SQLite only
    participates on (a) construction (hydrate + prune) and (b) each accept
    (INSERT OR IGNORE). A persistence failure is logged but does not block
    the request — the in-memory guard still works for the current process.
    """

    def __init__(
        self,
        max_size: int,
        window_seconds: float,
        db_path: Path,
        table_name: str,
    ) -> None:
        super().__init__(max_size, window_seconds)
        # Whitelist the table name — must be a simple identifier; SQLite
        # parameter binding does not cover table names, so we refuse anything
        # that is not [A-Za-z0-9_]+ to close off any injection vector even
        # though the caller is trusted.
        if not table_name.replace("_", "").isalnum():
            raise ValueError(f"unsafe table name: {table_name!r}")
        self._db_path = db_path
        self._table = table_name
        self._init_db()
        self._hydrate()

    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                f"CREATE TABLE IF NOT EXISTS {self._table} ("
                "  key TEXT PRIMARY KEY, "
                "  inserted_at REAL NOT NULL)"
            )
            conn.commit()

    def _hydrate(self) -> None:
        """Load within-window rows back into _seen; prune expired rows."""
        now_wall = time.time()
        cutoff_wall = now_wall - self._window_seconds
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                f"DELETE FROM {self._table} WHERE inserted_at <= ?",  # noqa: S608  # nosec B608 — _table is hardcoded class-constant, never user-input
                (cutoff_wall,),
            )
            rows = conn.execute(
                f"SELECT key, inserted_at FROM {self._table} ORDER BY inserted_at",  # noqa: S608  # nosec B608 — _table is hardcoded class-constant
            ).fetchall()
            conn.commit()
        # Translate wall-clock timestamps into monotonic-relative values so
        # the parent class' time.monotonic()-based pruning keeps working.
        now_mono = time.monotonic()
        with self._lock:
            for key, inserted_at in rows:
                age_seconds = now_wall - inserted_at
                self._seen[key] = now_mono - age_seconds
            # Enforce max_size even on hydrate — keeps oldest-first eviction.
            while len(self._seen) > self._max_size:
                self._seen.popitem(last=False)

    def check_and_record(self, payload_hash: str) -> bool:
        accepted = super().check_and_record(payload_hash)
        if accepted:
            try:
                with sqlite3.connect(self._db_path) as conn:
                    conn.execute(
                        f"INSERT OR IGNORE INTO {self._table} (key, inserted_at) VALUES (?, ?)",  # noqa: S608  # nosec B608 — _table is hardcoded class-constant
                        (payload_hash, time.time()),
                    )
                    conn.commit()
            except sqlite3.Error as exc:
                # Fail-open: in-memory guard still holds for this process.
                # Operator sees the drift via log + next hydrate will either
                # recover or keep surfacing the underlying DB issue.
                _logger.warning(
                    "replay_cache_persist_failed",
                    table=self._table,
                    key_prefix=payload_hash[:8],
                    error=str(exc),
                )
        return accepted

    def clear(self) -> None:
        super().clear()
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(f"DELETE FROM {self._table}")  # noqa: S608  # nosec B608 — _table is hardcoded class-constant
                conn.commit()
        except sqlite3.Error as exc:
            _logger.warning("replay_cache_clear_failed", table=self._table, error=str(exc))


_REPLAY_CACHE: ReplayCache | None = None
_EVENT_ID_CACHE: ReplayCache | None = None


def _build_cache(
    settings: TradingViewSettings,
    *,
    max_size: int,
    window_seconds: float,
    table_name: str,
) -> ReplayCache:
    if settings.webhook_replay_cache_persistent:
        return PersistentReplayCache(
            max_size=max_size,
            window_seconds=window_seconds,
            db_path=Path(settings.webhook_replay_cache_db_path),
            table_name=table_name,
        )
    return ReplayCache(max_size=max_size, window_seconds=window_seconds)


def _get_replay_cache(settings: TradingViewSettings) -> ReplayCache:
    global _REPLAY_CACHE
    if _REPLAY_CACHE is None:
        _REPLAY_CACHE = _build_cache(
            settings,
            max_size=settings.webhook_replay_cache_size,
            window_seconds=settings.webhook_replay_window_seconds,
            table_name="payload_seen",
        )
    return _REPLAY_CACHE


def _get_event_id_cache(settings: TradingViewSettings) -> ReplayCache:
    """V8.1: Layer-2 replay cache keyed on operator-supplied external event_id.

    Independent LRU — cache key space and TTL differ from the payload-hash
    cache, so sharing the instance would cross-pollute both. Shares the
    SQLite file with the payload cache when persistence is enabled (separate
    tables keep the key spaces cleanly apart).
    """
    global _EVENT_ID_CACHE
    if _EVENT_ID_CACHE is None:
        _EVENT_ID_CACHE = _build_cache(
            settings,
            max_size=settings.webhook_event_id_cache_size,
            window_seconds=settings.webhook_event_id_window_seconds,
            table_name="event_id_seen",
        )
    return _EVENT_ID_CACHE


def _reset_replay_cache_for_tests() -> None:
    """Test hook — clear singletons so each test starts clean."""
    global _REPLAY_CACHE, _EVENT_ID_CACHE
    _REPLAY_CACHE = None
    _EVENT_ID_CACHE = None


# D-193 / NEO-F-META-20260424-023: brute-force guard for the webhook auth
# pipeline. Independent bucket from app.security.auth so API-Key failures and
# webhook-credential failures do not cross-pollute each other. Threshold=0
# (from settings) disables the guard entirely.
_RATE_LIMITER: FailureTracker | None = None


def _get_rate_limiter(settings: TradingViewSettings) -> FailureTracker:
    global _RATE_LIMITER
    if _RATE_LIMITER is None:
        _RATE_LIMITER = FailureTracker(
            window_seconds=settings.webhook_rate_limit_window_seconds,
            threshold=settings.webhook_rate_limit_threshold,
        )
    return _RATE_LIMITER


def _reset_rate_limiter_for_tests() -> None:
    """Test hook — clear the webhook rate-limiter singleton."""
    global _RATE_LIMITER
    _RATE_LIMITER = None


def _verify_signature(raw_body: bytes, signature_header: str | None, secret: str) -> bool:
    if not signature_header or not secret:
        return False
    if not signature_header.startswith(_SIGNATURE_PREFIX):
        return False
    provided = signature_header[len(_SIGNATURE_PREFIX) :].strip()
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(provided, expected)


def _verify_shared_token(provided_token: str | None, expected_token: str) -> bool:
    """Constant-time equality check for the shared-token auth mode (TV-2.1).

    NOTE: shared-token mode does NOT verify body integrity — only that the
    caller knows the secret. Use HMAC mode whenever the client can compute it.
    """
    if not isinstance(provided_token, str) or not provided_token or not expected_token:
        return False
    return hmac.compare_digest(provided_token.strip(), expected_token)


def _authorize_request(
    raw_body: bytes,
    signature_header: str | None,
    token_header: str | None,
    settings: TradingViewSettings,
) -> tuple[bool, str]:
    """Return (accepted, auth_method_used).

    On failure, auth_method_used carries the rejection reason instead.

    V8-f: ``hmac_strict_event_id`` mode reuses the shared-token credential
    check at this layer. The strict body-binding (event_id + ts skew check)
    is enforced by ``_validate_strict_body_fields`` after JSON parsing.
    """
    mode = settings.webhook_auth_mode
    if settings.webhook_shared_token_disabled and mode in {
        "shared_token",
        "hmac_or_token",
        "hmac_strict_event_id",
    }:
        # Operator hard-off — short-circuit before constant-time compare.
        return False, "shared_token_disabled"
    if mode == "hmac":
        if _verify_signature(raw_body, signature_header, settings.webhook_secret):
            return True, "hmac"
        return False, "invalid_signature"
    if mode in {"shared_token", "hmac_strict_event_id"}:
        if _verify_shared_token(token_header, settings.webhook_shared_token):
            return True, "shared_token"
        return False, "invalid_shared_token"
    # hmac_or_token: try HMAC first (stronger), then fall back.
    if _verify_signature(raw_body, signature_header, settings.webhook_secret):
        return True, "hmac"
    if _verify_shared_token(token_header, settings.webhook_shared_token):
        return True, "shared_token"
    return False, "invalid_credentials"


def _validate_strict_body_fields(
    payload: object,
    *,
    now: datetime,
    skew_seconds: int,
) -> tuple[bool, str]:
    """V8-f strict-mode body validation.

    The shared-token-only path (``shared_token`` and ``hmac_or_token``) cannot
    bind the credential to the body — a leaked token replays any payload. The
    strict mode closes that gap by demanding two body fields:

    - ``event_id`` (>=8 chars, str) — Layer-2 dedup anchor; replay across
      restarts is already covered by D-189 PersistentReplayCache.
    - ``ts`` (ISO-8601 with timezone) — must fall within ``±skew_seconds`` of
      ``now``. Defends against captured-and-stalled replays beyond the
      rate-limit window.

    Returns ``(ok, reason)``. Reason is one of:
      ``ok``, ``not_a_dict``, ``missing_event_id``, ``event_id_too_short``,
      ``missing_ts``, ``invalid_ts``, ``clock_skew``.
    """
    if not isinstance(payload, dict):
        return False, "not_a_dict"
    raw_event_id = payload.get("event_id")
    if not isinstance(raw_event_id, str) or not raw_event_id.strip():
        return False, "missing_event_id"
    if len(raw_event_id.strip()) < 8:
        return False, "event_id_too_short"
    raw_ts = payload.get("ts")
    if not isinstance(raw_ts, str) or not raw_ts.strip():
        return False, "missing_ts"
    try:
        ts = datetime.fromisoformat(raw_ts.strip())
    except ValueError:
        return False, "invalid_ts"
    if ts.tzinfo is None:
        return False, "invalid_ts"
    delta = abs((now - ts).total_seconds())
    if delta > skew_seconds:
        return False, "clock_skew"
    return True, "ok"


_DEPRECATION_LOGGED = False


def _maybe_log_deprecation(mode: str) -> None:
    """V8-f: emit a one-shot warning when legacy token modes are active.

    ``shared_token`` and ``hmac_or_token`` accept any body once the token is
    valid — the strict mode is the supported migration target. Logged once
    per process to keep logs readable.
    """
    global _DEPRECATION_LOGGED
    if _DEPRECATION_LOGGED:
        return
    if mode in {"shared_token", "hmac_or_token"}:
        _logger.warning(
            "tradingview_webhook_auth_mode_deprecated",
            mode=mode,
            migration="hmac_strict_event_id or hmac (with proxy)",
            doc="docs/security/tv_webhook_migration.md",
        )
        _DEPRECATION_LOGGED = True


def _reset_deprecation_flag_for_tests() -> None:
    """Test hook — re-arm the one-shot deprecation warning."""
    global _DEPRECATION_LOGGED
    _DEPRECATION_LOGGED = False


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


PendingSignalWriter = Callable[[Path, TradingViewSignalEvent], None]
"""Injection hook for the pending-signal writer — tests replace this."""


def _default_pending_writer(path: Path, event: TradingViewSignalEvent) -> None:
    # SENTR-F-004: resolve the HMAC secret at write-time so operator
    # rotation via env-reload takes effect without router restart.
    secret = get_settings().tradingview.bridge_hmac_secret
    append_pending_signal(path, event, hmac_secret=secret)


_pending_writer: PendingSignalWriter = _default_pending_writer


def set_pending_signal_writer(writer: PendingSignalWriter) -> None:
    """Replace the pending-signal writer (test-only hook)."""
    global _pending_writer
    _pending_writer = writer


def reset_pending_signal_writer() -> None:
    global _pending_writer
    _pending_writer = _default_pending_writer


def _settings_gate(settings: AppSettings) -> TradingViewSettings:
    """Return TV-settings if endpoint is fully configured; otherwise 404.

    Fail-closed: an unconfigured or disabled webhook is indistinguishable
    from a non-existent endpoint. Required credentials depend on auth mode:
        hmac           -> webhook_secret
        shared_token   -> webhook_shared_token
        hmac_or_token  -> at least one of the two
    """
    tv = settings.tradingview
    if not tv.webhook_enabled:
        raise HTTPException(status_code=404, detail="Not Found")
    mode = tv.webhook_auth_mode
    has_secret = bool(tv.webhook_secret)
    has_token = bool(tv.webhook_shared_token)
    if mode == "hmac" and not has_secret:
        raise HTTPException(status_code=404, detail="Not Found")
    if mode == "shared_token" and not has_token:
        raise HTTPException(status_code=404, detail="Not Found")
    if mode == "hmac_or_token" and not (has_secret or has_token):
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
    x_kai_token: Annotated[str | None, Header(alias=_TOKEN_HEADER)] = None,
) -> dict[str, object]:
    """Accept a signed (or token-authenticated) TradingView alert payload.

    Returns 202 Accepted for valid, new payloads.
    401 for bad credentials. 409 for replayed payloads. 400 for malformed JSON.
    """
    request_id = _new_request_id()
    audit_path = Path(tv.webhook_audit_log)
    raw_body = await request.body()

    caller_ip = client_ip(request)
    common_log: dict[str, object] = {
        "request_id": request_id,
        "received_at": _utcnow_iso(),
        "source_ip": caller_ip or (request.client.host if request.client else None),
        "body_bytes": len(raw_body),
        "auth_mode": tv.webhook_auth_mode,
    }

    # D-193 / NEO-F-META-20260424-023: Brute-force guard BEFORE the auth
    # decision — a locked IP cannot even learn whether its credentials
    # would have been accepted. Threshold=0 disables the guard.
    rate_limiter = _get_rate_limiter(tv)
    locked, retry_after = rate_limiter.is_limited(caller_ip)
    if locked:
        entry = {
            **common_log,
            "outcome": "rejected",
            "reason": "rate_limited",
            "retry_after_seconds": retry_after,
        }
        _audit_writer(audit_path, entry)
        _logger.warning("tradingview_webhook_rate_limited", **entry)
        raise HTTPException(
            status_code=429,
            detail="Too Many Requests",
            headers={"Retry-After": str(retry_after)},
        )

    # TradingView cannot send custom HTTP headers in webhook alerts.
    # Fall back to extracting the token from the JSON body "token" field.
    body_token = x_kai_token
    if not body_token:
        try:
            body_obj = json.loads(raw_body.decode("utf-8")) if raw_body else {}
            if isinstance(body_obj, dict):
                candidate = body_obj.get("token") or body_obj.get("_token")
                # A JSON ``token`` field may be any type (int, list, dict). The
                # constant-time compare expects a string; coerce non-str values
                # to None so a malformed body fails auth cleanly (401) instead
                # of raising AttributeError → unauthenticated 500.
                body_token = candidate if isinstance(candidate, str) else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass

    accepted, auth_method = _authorize_request(raw_body, x_kai_signature, body_token, tv)
    if not accepted:
        failures = rate_limiter.record_failure(caller_ip)
        entry = {
            **common_log,
            "outcome": "rejected",
            "reason": auth_method,
            "rate_limit_failures": failures,
        }
        _audit_writer(audit_path, entry)
        _logger.warning("tradingview_webhook_rejected", **entry)
        raise HTTPException(status_code=401, detail="Invalid credentials")
    rate_limiter.reset(caller_ip)
    common_log["auth_method"] = auth_method
    _maybe_log_deprecation(tv.webhook_auth_mode)

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

    # Strip auth token from payload before routing/logging (never persist secrets).
    if isinstance(parsed_payload, dict):
        parsed_payload.pop("token", None)
        parsed_payload.pop("_token", None)

    # V8-f strict-mode body binding: when shared-token auth is in strict mode,
    # the body MUST carry event_id + ts within skew. Failure is a 401 (auth-
    # bucket Brute-Force-Guard increments) and an audit row keyed by reason.
    if tv.webhook_auth_mode == "hmac_strict_event_id" and auth_method == "shared_token":
        ok, strict_reason = _validate_strict_body_fields(
            parsed_payload,
            now=datetime.now(UTC),
            skew_seconds=tv.webhook_strict_ts_skew_seconds,
        )
        if not ok:
            failures = rate_limiter.record_failure(caller_ip)
            entry = {
                **common_log,
                "outcome": "rejected",
                "reason": f"strict_{strict_reason}",
                "payload_hash": payload_hash,
                "rate_limit_failures": failures,
            }
            _audit_writer(audit_path, entry)
            _logger.warning("tradingview_webhook_strict_rejected", **entry)
            raise HTTPException(status_code=401, detail="Strict mode rejected")

    # V8.1 Layer-2 replay guard: operator-supplied external event_id, cached
    # independently from the byte-level payload hash. Pass-through when the
    # alert body has no event_id field — layer-1 remains the sole guard.
    external_event_id = extract_external_event_id(
        parsed_payload if isinstance(parsed_payload, dict) else {}
    )
    if external_event_id is not None:
        event_cache = _get_event_id_cache(tv)
        if not event_cache.check_and_record(external_event_id):
            entry = {
                **common_log,
                "outcome": "rejected",
                "reason": "event_id_replay",
                "payload_hash": payload_hash,
                "external_event_id": external_event_id,
                "dedup_layer": "external_event_id",
            }
            _audit_writer(audit_path, entry)
            _logger.info("tradingview_webhook_event_id_replay", **entry)
            raise HTTPException(status_code=409, detail="Replay detected (event_id)")

    routing_outcome: dict[str, object] = {"enabled": tv.webhook_signal_routing_enabled}
    pipeline_event: TradingViewSignalEvent | None = None
    if tv.webhook_signal_routing_enabled:
        try:
            pipeline_event = normalize_tradingview_payload(
                parsed_payload if isinstance(parsed_payload, dict) else {},
                request_id=request_id,
                payload_hash=payload_hash,
                received_at=common_log["received_at"],  # type: ignore[arg-type]
                external_event_id=external_event_id,
                auth_method=auth_method,
            )
        except NormalizationError as exc:
            routing_outcome["status"] = "normalize_failed"
            routing_outcome["reason"] = str(exc)[:200]
        else:
            try:
                _pending_writer(Path(tv.webhook_pending_signals_log), pipeline_event)
            except OSError as exc:
                routing_outcome["status"] = "emit_failed"
                routing_outcome["reason"] = str(exc)[:200]
                pipeline_event = None
            else:
                routing_outcome["status"] = "emitted"
                routing_outcome["event_id"] = pipeline_event.event_id
                routing_outcome["signal_path_id"] = pipeline_event.provenance.signal_path_id
    else:
        routing_outcome["status"] = "disabled"

    signal_path_id = (
        pipeline_event.provenance.signal_path_id if pipeline_event is not None else None
    )
    entry = {
        **common_log,
        "outcome": "accepted",
        "payload_hash": payload_hash,
        "external_event_id": external_event_id,
        "payload": parsed_payload,
        "provenance": {
            "source": "tradingview_webhook",
            "version": "tv-3" if pipeline_event is not None else "tv-1",
            "signal_path_id": signal_path_id,
            "auth_method": auth_method,
        },
        "routing": routing_outcome,
    }
    _audit_writer(audit_path, entry)
    _logger.info(
        "tradingview_webhook_accepted",
        request_id=request_id,
        payload_hash=payload_hash,
        body_bytes=len(raw_body),
        routing_status=routing_outcome.get("status"),
    )
    response: dict[str, object] = {
        "status": "accepted",
        "request_id": request_id,
        "received_at": entry["received_at"],
    }
    if pipeline_event is not None:
        response["event_id"] = pipeline_event.event_id
        response["signal_path_id"] = pipeline_event.provenance.signal_path_id
    return response
