"""FastAPI operator surface exposing canonical read/control summaries.

Security and scope invariants:
- No business logic: delegates to canonical MCP-backed surfaces.
- Read surfaces are read-only payload passthroughs.
- Guarded run-once is explicit paper/shadow control only.
- Fail closed when APP_API_KEY is not configured.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.agents import mcp_server
from app.core.settings import AppSettings, get_settings

_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
_GUARDED_AUDIT_PATH = Path("artifacts/operator_api_guarded_audit.jsonl")
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_IDEMPOTENCY_CACHE_MAX = 256
# These constants are used as module-level defaults.  The actual rate-limit
# values are overridden at startup from AppSettings (see _init_guard_state()).
_GUARDED_RATE_LIMIT_WINDOW_SECONDS = 30.0
_GUARDED_RATE_LIMIT_MAX_REQUESTS = 5

class IdempotencyStore:
    """In-memory idempotency cache for guarded operator requests.

    WARNING: Single-instance only. In a multi-process or clustered deployment
    each process maintains its own independent cache, so idempotency guarantees
    are NOT preserved across instances. Replace with a shared backend (e.g. Redis)
    before scaling beyond a single-process deployment.
    """

    def __init__(self, max_size: int = 256) -> None:
        self._max_size = max_size
        self._lock = Lock()
        self._cache: OrderedDict[str, _IdempotencyRecord] = OrderedDict()

    def get(self, key: str) -> _IdempotencyRecord | None:
        with self._lock:
            record = self._cache.get(key)
            if record is not None:
                self._cache.move_to_end(key)
            return record

    def set(self, key: str, record: _IdempotencyRecord) -> None:
        with self._lock:
            self._cache[key] = record
            self._cache.move_to_end(key)
            if len(self._cache) > self._max_size:
                self._cache.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


class RateLimitStore:
    """In-memory sliding-window rate limiter for guarded operator endpoints.

    WARNING: Single-instance only. Subject to the same cluster limitations as
    IdempotencyStore — rate limits are not enforced across multiple processes.
    Replace with a shared backend before scaling.
    """

    def __init__(self, window_seconds: float, max_requests: int) -> None:
        self._window_seconds = window_seconds
        self._max_requests = max_requests
        self._lock = Lock()
        self._buckets: dict[str, deque[float]] = {}

    def check_and_record(self, subject: str) -> bool:
        """Return True if the request is allowed, False if rate-limited."""
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.setdefault(subject, deque())
            cutoff = now - self._window_seconds
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self._max_requests:
                return False
            bucket.append(now)
            return True

    def clear(self) -> None:
        with self._lock:
            self._buckets.clear()


def _build_guard_stores(
    *,
    rate_limit_window_seconds: float = _GUARDED_RATE_LIMIT_WINDOW_SECONDS,
    rate_limit_max_requests: int = _GUARDED_RATE_LIMIT_MAX_REQUESTS,
    idempotency_cache_max: int = _IDEMPOTENCY_CACHE_MAX,
) -> tuple[IdempotencyStore, RateLimitStore]:
    return (
        IdempotencyStore(max_size=idempotency_cache_max),
        RateLimitStore(
            window_seconds=rate_limit_window_seconds,
            max_requests=rate_limit_max_requests,
        ),
    )


_IDEMPOTENCY_STORE, _RATE_LIMIT_STORE = _build_guard_stores()



@dataclass(frozen=True)
class _IdempotencyRecord:
    request_fingerprint: str
    response_payload: dict[str, object]


class TradingLoopRunOnceRequest(BaseModel):
    symbol: str = "BTC/USDT"
    mode: str = "paper"
    provider: str = "mock"
    analysis_profile: str = "conservative"
    loop_audit_path: str = "artifacts/trading_loop_audit.jsonl"
    execution_audit_path: str = "artifacts/paper_execution_audit.jsonl"
    freshness_threshold_seconds: float = Field(default=120.0, gt=0.0)
    timeout_seconds: int = Field(default=10, ge=1)


def _new_context_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _normalize_context_id(value: str | None, *, prefix: str) -> str:
    candidate = (value or "").strip()
    if candidate and _REQUEST_ID_PATTERN.fullmatch(candidate):
        return candidate
    return _new_context_id(prefix)


def _get_request_id(request: Request) -> str:
    request_id = getattr(request.state, "operator_request_id", None)
    if isinstance(request_id, str) and request_id:
        return request_id
    generated = _new_context_id("req")
    request.state.operator_request_id = generated
    return generated


def _get_correlation_id(request: Request) -> str:
    correlation_id = getattr(request.state, "operator_correlation_id", None)
    if isinstance(correlation_id, str) and correlation_id:
        return correlation_id
    fallback = _get_request_id(request)
    request.state.operator_correlation_id = fallback
    return fallback


def _set_context_headers(response: Response, request: Request) -> None:
    response.headers["X-Request-ID"] = _get_request_id(request)
    response.headers["X-Correlation-ID"] = _get_correlation_id(request)


def _build_error_payload(
    request: Request,
    *,
    code: str,
    message: str,
) -> dict[str, object]:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": _get_request_id(request),
            "correlation_id": _get_correlation_id(request),
        },
        "execution_enabled": False,
        "write_back_allowed": False,
    }


def _extract_error_code(exc: HTTPException) -> str:
    detail = exc.detail
    if isinstance(detail, dict):
        error = detail.get("error")
        if isinstance(error, dict):
            code = error.get("code")
            if isinstance(code, str) and code:
                return code
    return "operator_api_error"


def _operator_http_error(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    include_www_authenticate: bool = False,
    retry_after_seconds: int | None = None,
) -> HTTPException:
    headers: dict[str, str] = {
        "X-Request-ID": _get_request_id(request),
        "X-Correlation-ID": _get_correlation_id(request),
    }
    if include_www_authenticate:
        headers["WWW-Authenticate"] = "Bearer"
    if retry_after_seconds is not None:
        headers["Retry-After"] = str(retry_after_seconds)
    return HTTPException(
        status_code=status_code,
        detail=_build_error_payload(request, code=code, message=message),
        headers=headers,
    )


def bind_operator_request_context(
    request: Request,
    x_request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
    x_correlation_id: Annotated[str | None, Header(alias="X-Correlation-ID")] = None,
) -> None:
    request_id = _normalize_context_id(x_request_id, prefix="req")
    correlation_id = (
        _normalize_context_id(x_correlation_id, prefix="corr")
        if x_correlation_id is not None
        else request_id
    )
    request.state.operator_request_id = request_id
    request.state.operator_correlation_id = correlation_id


def _guarded_audit_log_path() -> Path:
    return (_WORKSPACE_ROOT / _GUARDED_AUDIT_PATH).resolve()


def _append_guarded_audit(
    *,
    request: Request,
    payload: TradingLoopRunOnceRequest,
    idempotency_key: str,
    outcome: str,
    error_code: str | None = None,
    idempotency_replayed: bool = False,
) -> None:
    """Append guarded endpoint audit rows without raising caller-visible errors."""
    audit_path = _guarded_audit_log_path()
    row = {
        "timestamp_utc": datetime.now(tz=UTC).isoformat(),
        "event": "operator_guarded_request",
        "endpoint": "/operator/trading-loop/run-once",
        "request_id": _get_request_id(request),
        "correlation_id": _get_correlation_id(request),
        "idempotency_key": idempotency_key,
        "outcome": outcome,
        "error_code": error_code,
        "idempotency_replayed": idempotency_replayed,
        "symbol": payload.symbol,
        "mode": payload.mode,
        "provider": payload.provider,
        "analysis_profile": payload.analysis_profile,
        "execution_enabled": False,
        "write_back_allowed": False,
    }
    try:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with audit_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:
        # Best-effort audit append to avoid surfacing file I/O internals to API callers.
        return


def _validate_idempotency_key(request: Request, key: str | None) -> str:
    candidate = (key or "").strip()
    if not candidate:
        raise _operator_http_error(
            request,
            status_code=400,
            code="missing_idempotency_key",
            message="Idempotency-Key header is required for guarded requests",
        )
    if _IDEMPOTENCY_KEY_PATTERN.fullmatch(candidate) is None:
        raise _operator_http_error(
            request,
            status_code=400,
            code="invalid_idempotency_key",
            message="Idempotency-Key must match [A-Za-z0-9._:-]{1,128}",
        )
    return candidate


def _request_fingerprint(payload: TradingLoopRunOnceRequest) -> str:
    canonical = json.dumps(
        payload.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _load_idempotent_replay(
    request: Request,
    *,
    idempotency_key: str,
    fingerprint: str,
) -> dict[str, object] | None:
    record = _IDEMPOTENCY_STORE.get(idempotency_key)
    if record is None:
        return None

    if record.request_fingerprint != fingerprint:
        raise _operator_http_error(
            request,
            status_code=409,
            code="idempotency_key_conflict",
            message="Idempotency-Key was already used with a different payload",
        )

    replay_payload = dict(record.response_payload)
    replay_payload["idempotency_replayed"] = True
    return replay_payload


def _store_idempotent_response(
    *,
    idempotency_key: str,
    fingerprint: str,
    response_payload: dict[str, object],
) -> None:
    stored_payload = dict(response_payload)
    stored_payload["idempotency_replayed"] = False
    _IDEMPOTENCY_STORE.set(
        idempotency_key,
        _IdempotencyRecord(
            request_fingerprint=fingerprint,
            response_payload=stored_payload,
        ),
    )


def _enforce_guarded_rate_limit(request: Request) -> None:
    subject = getattr(request.state, "operator_subject", "operator_unknown")
    allowed = _RATE_LIMIT_STORE.check_and_record(subject)
    if not allowed:
        retry_after = int(_RATE_LIMIT_STORE._window_seconds)
        raise _operator_http_error(
            request,
            status_code=429,
            code="guarded_rate_limited",
            message=(
                f"Too many guarded requests; retry after {retry_after} seconds"
            ),
            retry_after_seconds=retry_after,
        )


async def _resolve_read_payload(
    request: Request,
    response: Response,
    *,
    error_code: str,
    loader: Callable[[], Awaitable[dict[str, object]]],
) -> dict[str, object]:
    _set_context_headers(response, request)
    try:
        return await loader()
    except Exception as exc:
        raise _operator_http_error(
            request,
            status_code=503,
            code=error_code,
            message=f"Operator read surface unavailable: {exc.__class__.__name__}",
        ) from exc


def _reset_operator_guard_state_for_tests() -> None:
    """Reset in-memory guard state for deterministic unit tests."""
    _IDEMPOTENCY_STORE.clear()
    _RATE_LIMIT_STORE.clear()


def require_operator_api_token(
    request: Request,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    settings: AppSettings = Depends(get_settings),  # noqa: B008
) -> None:
    """Require a configured APP_API_KEY and a matching Bearer token."""
    api_key = (settings.api_key or "").strip()
    if not api_key:
        raise _operator_http_error(
            request,
            status_code=503,
            code="operator_api_disabled",
            message="Operator API is disabled until APP_API_KEY is configured (fail-closed)",
        )

    if not authorization:
        raise _operator_http_error(
            request,
            status_code=401,
            code="missing_authorization_header",
            message="Missing Authorization header",
            include_www_authenticate=True,
        )

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _operator_http_error(
            request,
            status_code=401,
            code="invalid_authorization_scheme",
            message="Invalid Authorization scheme",
            include_www_authenticate=True,
        )

    if not secrets.compare_digest(token.strip(), api_key):
        raise _operator_http_error(
            request,
            status_code=403,
            code="invalid_api_key",
            message="Invalid API key",
        )

    token_fingerprint = hashlib.sha256(token.strip().encode("utf-8")).hexdigest()[:16]
    request.state.operator_subject = f"token_{token_fingerprint}"


router = APIRouter(
    prefix="/operator",
    tags=["operator"],
    dependencies=[
        Depends(bind_operator_request_context),
        Depends(require_operator_api_token),
    ],
)


@router.get("/status")
async def get_operator_status(request: Request, response: Response) -> dict[str, object]:
    """Canonical operator status surface (read-only readiness projection)."""
    return await _resolve_read_payload(
        request,
        response,
        error_code="status_unavailable",
        loader=mcp_server.get_operational_readiness_summary,
    )


@router.get("/readiness")
async def get_operator_readiness(request: Request, response: Response) -> dict[str, object]:
    """Canonical operator readiness surface (read-only)."""
    return await _resolve_read_payload(
        request,
        response,
        error_code="readiness_unavailable",
        loader=mcp_server.get_operational_readiness_summary,
    )


@router.get("/decision-pack")
async def get_operator_decision_pack(request: Request, response: Response) -> dict[str, object]:
    """Canonical operator decision-pack surface (read-only)."""
    return await _resolve_read_payload(
        request,
        response,
        error_code="decision_pack_unavailable",
        loader=mcp_server.get_decision_pack_summary,
    )


@router.get("/daily-summary")
async def get_operator_daily_summary(
    request: Request,
    response: Response,
    handoff_path: str | None = None,
    state_path: str = "artifacts/active_route_profile.json",
    alert_audit_dir: str = "artifacts",
    artifacts_dir: str = "artifacts",
    stale_after_hours: int = 24,
    stale_after_days: float = 30.0,
    loop_audit_path: str = "artifacts/trading_loop_audit.jsonl",
    loop_last_n: int = 50,
    portfolio_audit_path: str = "artifacts/paper_execution_audit.jsonl",
    market_data_provider: str = "coingecko",
    freshness_threshold_seconds: float = 120.0,
    timeout_seconds: int = 10,
    review_journal_path: str = "artifacts/operator_review_journal.jsonl",
) -> dict[str, object]:
    """Canonical operator daily summary surface (read-only)."""
    return await _resolve_read_payload(
        request,
        response,
        error_code="daily_summary_unavailable",
        loader=lambda: mcp_server.get_daily_operator_summary(
            handoff_path=handoff_path,
            state_path=state_path,
            alert_audit_dir=alert_audit_dir,
            artifacts_dir=artifacts_dir,
            stale_after_hours=stale_after_hours,
            retention_stale_after_days=stale_after_days,
            loop_audit_path=loop_audit_path,
            loop_last_n=loop_last_n,
            portfolio_audit_path=portfolio_audit_path,
            market_data_provider=market_data_provider,
            freshness_threshold_seconds=freshness_threshold_seconds,
            timeout_seconds=timeout_seconds,
            review_journal_path=review_journal_path,
        ),
    )


@router.get("/review-journal")
async def get_operator_review_journal(
    request: Request,
    response: Response,
    journal_path: str = "artifacts/operator_review_journal.jsonl",
) -> dict[str, object]:
    """Canonical operator review-journal surface (read-only)."""
    return await _resolve_read_payload(
        request,
        response,
        error_code="review_journal_unavailable",
        loader=lambda: mcp_server.get_review_journal_summary(
            journal_path=journal_path,
        ),
    )


@router.get("/resolution-summary")
async def get_operator_resolution_summary(
    request: Request,
    response: Response,
    journal_path: str = "artifacts/operator_review_journal.jsonl",
) -> dict[str, object]:
    """Canonical operator resolution summary surface (read-only)."""
    return await _resolve_read_payload(
        request,
        response,
        error_code="resolution_summary_unavailable",
        loader=lambda: mcp_server.get_resolution_summary(
            journal_path=journal_path,
        ),
    )


@router.get("/alert-audit")
async def get_operator_alert_audit(
    request: Request,
    response: Response,
    audit_dir: str = "artifacts",
) -> dict[str, object]:
    """Canonical operator alert audit summary surface (read-only)."""
    return await _resolve_read_payload(
        request,
        response,
        error_code="alert_audit_unavailable",
        loader=lambda: mcp_server.get_alert_audit_summary(
            audit_dir=audit_dir,
        ),
    )


@router.get("/portfolio-snapshot")
async def get_operator_portfolio_snapshot(
    request: Request,
    response: Response,
    audit_path: str = "artifacts/paper_execution_audit.jsonl",
    provider: str = "coingecko",
    freshness_threshold_seconds: float = 120.0,
    timeout_seconds: int = 10,
) -> dict[str, object]:
    """Canonical read-only paper portfolio snapshot."""
    return await _resolve_read_payload(
        request,
        response,
        error_code="portfolio_snapshot_unavailable",
        loader=lambda: mcp_server.get_paper_portfolio_snapshot(
            audit_path=audit_path,
            provider=provider,
            freshness_threshold_seconds=freshness_threshold_seconds,
            timeout_seconds=timeout_seconds,
        ),
    )


@router.get("/exposure-summary")
async def get_operator_exposure_summary(
    request: Request,
    response: Response,
    audit_path: str = "artifacts/paper_execution_audit.jsonl",
    provider: str = "coingecko",
    freshness_threshold_seconds: float = 120.0,
    timeout_seconds: int = 10,
) -> dict[str, object]:
    """Canonical read-only paper exposure summary."""
    return await _resolve_read_payload(
        request,
        response,
        error_code="exposure_summary_unavailable",
        loader=lambda: mcp_server.get_paper_exposure_summary(
            audit_path=audit_path,
            provider=provider,
            freshness_threshold_seconds=freshness_threshold_seconds,
            timeout_seconds=timeout_seconds,
        ),
    )


@router.get("/trading-loop/status")
async def get_operator_trading_loop_status(
    request: Request,
    response: Response,
    audit_path: str = "artifacts/trading_loop_audit.jsonl",
    mode: str = "paper",
) -> dict[str, object]:
    """Canonical read-only trading-loop status summary."""
    return await _resolve_read_payload(
        request,
        response,
        error_code="trading_loop_status_unavailable",
        loader=lambda: mcp_server.get_trading_loop_status(
            audit_path=audit_path,
            mode=mode,
        ),
    )


@router.get("/trading-loop/recent-cycles")
async def get_operator_recent_trading_cycles(
    request: Request,
    response: Response,
    audit_path: str = "artifacts/trading_loop_audit.jsonl",
    last_n: int = 20,
) -> dict[str, object]:
    """Canonical read-only recent trading-loop cycle summary."""
    return await _resolve_read_payload(
        request,
        response,
        error_code="recent_cycles_unavailable",
        loader=lambda: mcp_server.get_recent_trading_cycles(
            audit_path=audit_path,
            last_n=last_n,
        ),
    )


@router.post("/trading-loop/run-once")
async def post_operator_trading_loop_run_once(
    payload: TradingLoopRunOnceRequest,
    request: Request,
    response: Response,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, object]:
    """Guarded trading-loop run-once endpoint (paper/shadow only)."""
    _set_context_headers(response, request)
    safe_idempotency_key = (idempotency_key or "").strip() or "<missing>"

    try:
        safe_idempotency_key = _validate_idempotency_key(request, idempotency_key)
        fingerprint = _request_fingerprint(payload)

        replay_payload = _load_idempotent_replay(
            request,
            idempotency_key=safe_idempotency_key,
            fingerprint=fingerprint,
        )
        if replay_payload is not None:
            _append_guarded_audit(
                request=request,
                payload=payload,
                idempotency_key=safe_idempotency_key,
                outcome="idempotency_replay",
                idempotency_replayed=True,
            )
            return replay_payload

        _enforce_guarded_rate_limit(request)
    except HTTPException as exc:
        _append_guarded_audit(
            request=request,
            payload=payload,
            idempotency_key=safe_idempotency_key,
            outcome="rejected",
            error_code=_extract_error_code(exc),
        )
        raise

    try:
        result = await mcp_server.run_trading_loop_once(
            symbol=payload.symbol,
            mode=payload.mode,
            provider=payload.provider,
            analysis_profile=payload.analysis_profile,
            loop_audit_path=payload.loop_audit_path,
            execution_audit_path=payload.execution_audit_path,
            freshness_threshold_seconds=payload.freshness_threshold_seconds,
            timeout_seconds=payload.timeout_seconds,
        )
    except ValueError as exc:
        _append_guarded_audit(
            request=request,
            payload=payload,
            idempotency_key=safe_idempotency_key,
            outcome="rejected",
            error_code="guarded_request_rejected",
        )
        raise _operator_http_error(
            request,
            status_code=400,
            code="guarded_request_rejected",
            message=str(exc),
        ) from exc
    except Exception as exc:
        _append_guarded_audit(
            request=request,
            payload=payload,
            idempotency_key=safe_idempotency_key,
            outcome="failed",
            error_code="guarded_request_failed",
        )
        raise _operator_http_error(
            request,
            status_code=503,
            code="guarded_request_failed",
            message=f"Guarded request failed: {exc.__class__.__name__}",
        ) from exc

    response_payload = dict(result)
    response_payload["idempotency_replayed"] = False

    _store_idempotent_response(
        idempotency_key=safe_idempotency_key,
        fingerprint=fingerprint,
        response_payload=response_payload,
    )
    _append_guarded_audit(
        request=request,
        payload=payload,
        idempotency_key=safe_idempotency_key,
        outcome="accepted",
    )

    return response_payload
