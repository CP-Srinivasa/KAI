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
            message=(f"Too many guarded requests; retry after {retry_after} seconds"),
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
    cf_access_email: Annotated[
        str | None, Header(alias="Cf-Access-Authenticated-User-Email")
    ] = None,
    settings: AppSettings = Depends(get_settings),  # noqa: B008
) -> None:
    """Require either a CF-Access identity (allowlisted email) or a Bearer APP_API_KEY.

    See ``app.security.auth`` for the global middleware that applies the same
    two-mechanism check. Per-route enforcement here mirrors it so operator
    endpoints stay independently fail-closed.
    """
    api_key = (settings.api_key or "").strip()
    if not api_key:
        raise _operator_http_error(
            request,
            status_code=503,
            code="operator_api_disabled",
            message="Operator API is disabled until APP_API_KEY is configured (fail-closed)",
        )

    # (1) Cloudflare Access — trusted email forwarded by the tunnel.
    cf_allowed = {
        e.strip().lower() for e in (settings.cf_access_allowed_emails or "").split(",") if e.strip()
    }
    if cf_allowed and cf_access_email:
        email = cf_access_email.strip().lower()
        if email in cf_allowed:
            request.state.operator_subject = f"cf_{email}"
            return

    # (2) Bearer token — local scripts / cron / freshness probe.
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
    """Canonical operator status surface (read-only)."""
    return await _resolve_read_payload(
        request,
        response,
        error_code="status_unavailable",
        loader=mcp_server.get_daily_operator_summary,
    )


@router.get("/readiness")
async def get_operator_readiness(request: Request, response: Response) -> dict[str, object]:
    """Canonical operator readiness surface (read-only)."""
    return await _resolve_read_payload(
        request,
        response,
        error_code="readiness_unavailable",
        loader=mcp_server.get_daily_operator_summary,
    )


@router.get("/decision-pack")
async def get_operator_decision_pack(request: Request, response: Response) -> dict[str, object]:
    """Canonical operator decision-pack surface (read-only)."""
    return await _resolve_read_payload(
        request,
        response,
        error_code="decision_pack_unavailable",
        loader=mcp_server.get_daily_operator_summary,
    )


@router.get("/daily-summary")
async def get_operator_daily_summary(
    request: Request,
    response: Response,
) -> dict[str, object]:
    """Canonical operator daily summary surface (read-only)."""
    return await _resolve_read_payload(
        request,
        response,
        error_code="daily_summary_unavailable",
        loader=mcp_server.get_daily_operator_summary,
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
        loader=mcp_server.get_daily_operator_summary,
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
        loader=mcp_server.get_daily_operator_summary,
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


@router.get("/portfolio/realized-by-asset")
async def get_realized_by_asset(
    request: Request,
    response: Response,
    audit_path: str = "artifacts/paper_execution_audit.jsonl",
) -> dict[str, object]:
    """Per-asset realized PnL from paper_execution_audit.jsonl.

    2026-05-25 Forensik-Antwort: Diese Route widerlegt die Annahme "Vor
    Live-Mode keine sinnvolle Visualisierung" und entkoppelt die UI von
    "Phase 2 — nach Backtest-Endpoint". Reine Aggregation über
    position_closed + position_partial_closed Events. KEIN Exchange-Call,
    KEIN Live-Trading, KEIN Mark-to-Market, read-only.

    Schema: siehe app.execution.portfolio_read.compute_realized_by_asset.
    """
    from app.execution.portfolio_read import compute_realized_by_asset

    _set_context_headers(response, request)
    return compute_realized_by_asset(Path(audit_path))


def _realized_summary_block(by_asset_summary: dict[str, object]) -> dict[str, object]:
    """Extract realized_summary fields from compute_realized_by_asset output.

    mypy-safe: by_asset_summary is dict[str, object] (untyped JSON shape), so
    we narrow ``totals`` via isinstance and treat missing/non-dict as zero.
    """
    totals_raw = by_asset_summary.get("totals")
    totals: dict[str, object] = totals_raw if isinstance(totals_raw, dict) else {}
    return {
        "total_realized_pnl_usd": totals.get("realized_pnl_usd", 0.0),
        "closed_trades": totals.get("closed_trades", 0),
        "assets_count": totals.get("assets_count", 0),
        "last_close_utc": by_asset_summary.get("audit_last_event_utc"),
    }


@router.get("/paper-pipeline-status")
async def get_paper_pipeline_status(
    request: Request,
    response: Response,
    audit_path: str = "artifacts/paper_execution_audit.jsonl",
    loop_audit_path: str = "artifacts/trading_loop_audit.jsonl",
    blocked_alerts_path: str = "artifacts/blocked_alerts.jsonl",
    cron_log_path: str = "artifacts/paper_trading_cron.log",
    bridge_orders_path: str = "artifacts/bridge_pending_orders.jsonl",
) -> dict[str, object]:
    """Operator-Diagnose: ist die Paper-Pipeline lebendig oder eingefroren?

    2026-05-25 Forensik-Antwort: Operator-Eindruck "Equity bewegt sich nicht"
    wird durch fünf orthogonale Indikatoren erklärt — letzter Fill, letzter
    Close, Cron-Heartbeat, Bridge-Status, Eligibility-Block-Reasons in den
    letzten 24h. Keine Behauptung "OK"/"down", nur transparente Zahlen.

    KEINE Exchange-Calls, KEINE Live-Daten.
    """
    from app.execution.portfolio_read import compute_realized_by_asset

    _set_context_headers(response, request)

    audit = Path(audit_path)
    loop_audit = Path(loop_audit_path)
    blocked = Path(blocked_alerts_path)
    cron = Path(cron_log_path)
    bridge = Path(bridge_orders_path)
    now = datetime.now(UTC)

    def _age_seconds(p: Path) -> float | None:
        if not p.exists():
            return None
        return now.timestamp() - p.stat().st_mtime

    def _last_event_ts(p: Path, *, type_filter: set[str] | None = None) -> str | None:
        if not p.exists():
            return None
        last: str | None = None
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(d, dict):
                    continue
                if type_filter is not None and d.get("event_type") not in type_filter:
                    continue
                ts = d.get("timestamp_utc") or d.get("created_at") or d.get("filled_at")
                if isinstance(ts, str) and ts and (last is None or ts > last):
                    last = ts
        except OSError:
            return None
        return last

    # Block-Reason-Counter aus blocked_alerts.jsonl (letzte 24h).
    cutoff = now.timestamp() - 24 * 3600
    block_reasons: dict[str, int] = {}
    if blocked.exists():
        try:
            for line in blocked.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(d, dict):
                    continue
                ts = d.get("blocked_at", "")
                if not isinstance(ts, str):
                    continue
                try:
                    ts_clean = ts.replace("Z", "+00:00") if ts.endswith("Z") else ts
                    ts_dt = datetime.fromisoformat(ts_clean)
                except ValueError:
                    continue
                if ts_dt.timestamp() < cutoff:
                    continue
                reason = d.get("block_reason", "unknown")
                if isinstance(reason, str):
                    block_reasons[reason] = block_reasons.get(reason, 0) + 1
        except OSError:
            pass

    # Cron-Log: priority_rejected vs completed in den letzten 1000 Zeilen.
    cron_recent_priority_rejected = 0
    cron_recent_completed = 0
    cron_recent_total = 0
    if cron.exists():
        try:
            lines = cron.read_text(encoding="utf-8").splitlines()[-1000:]
            for line in lines:
                if "status=priority_rejected" in line:
                    cron_recent_priority_rejected += 1
                    cron_recent_total += 1
                elif "status=completed" in line:
                    cron_recent_completed += 1
                    cron_recent_total += 1
                elif "status=" in line:
                    cron_recent_total += 1
        except OSError:
            pass

    # Replay-Health: schickt skipped_events transparent durch.
    try:
        from app.execution.audit_replay import replay_paper_audit

        replay = replay_paper_audit(audit)
        replay_payload = {
            "available": replay.available,
            "error": replay.error,
            "cash_usd": round(replay.cash_usd, 4),
            "open_positions": sorted(replay.positions.keys()),
            "open_positions_count": len(replay.positions),
            "skipped_events": [{"line": ln, "reason": r} for ln, r in replay.skipped_events],
        }
    except Exception as exc:  # noqa: BLE001
        replay_payload = {
            "available": False,
            "error": f"replay_exception:{exc.__class__.__name__}",
        }

    # Realized-by-asset Summary (totals only — full details an separater Route).
    by_asset_summary = compute_realized_by_asset(audit)

    age_audit = _age_seconds(audit)
    age_loop = _age_seconds(loop_audit)
    age_cron = _age_seconds(cron)
    age_bridge = _age_seconds(bridge)

    last_fill = _last_event_ts(audit, type_filter={"order_filled"})
    last_close = _last_event_ts(audit, type_filter={"position_closed", "position_partial_closed"})
    last_order = _last_event_ts(audit, type_filter={"order_created"})

    return {
        "as_of_utc": now.isoformat(),
        "audit_files": {
            "paper_execution_audit": {
                "path": str(audit),
                "exists": audit.exists(),
                "age_seconds": age_audit,
                "last_order_created_utc": last_order,
                "last_order_filled_utc": last_fill,
                "last_position_close_utc": last_close,
            },
            "trading_loop_audit": {
                "path": str(loop_audit),
                "exists": loop_audit.exists(),
                "age_seconds": age_loop,
            },
            "paper_trading_cron_log": {
                "path": str(cron),
                "exists": cron.exists(),
                "age_seconds": age_cron,
            },
            "bridge_pending_orders": {
                "path": str(bridge),
                "exists": bridge.exists(),
                "age_seconds": age_bridge,
            },
        },
        "replay_health": replay_payload,
        "cron_recent_1000": {
            "total_status_rows": cron_recent_total,
            "priority_rejected": cron_recent_priority_rejected,
            "completed": cron_recent_completed,
            "priority_rejected_share_pct": round(
                cron_recent_priority_rejected / cron_recent_total * 100.0, 2
            )
            if cron_recent_total > 0
            else None,
        },
        "block_reasons_24h": block_reasons,
        "block_total_24h": sum(block_reasons.values()),
        "realized_summary": _realized_summary_block(by_asset_summary),
        "freeze_indicators": {
            "paper_audit_stale_seconds": age_audit,
            "no_fills_since_seconds": (
                (now - datetime.fromisoformat(last_fill.replace("Z", "+00:00"))).total_seconds()
                if isinstance(last_fill, str) and last_fill
                else None
            ),
            "all_cron_priority_rejected": (
                cron_recent_total > 0 and cron_recent_priority_rejected == cron_recent_total
            ),
        },
    }


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
