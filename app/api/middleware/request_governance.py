"""API request governance middleware.

Provides:
- Request-ID generation and propagation (X-Request-ID)
- API request audit logging (append-only JSONL) including client_ip
- Request body-size enforcement (HTTP 413 when exceeded)
- Consistent error response model

Security invariants:
- Read-only output (no mutation)
- Fail-closed on audit write errors (log, don't crash)
- No trading semantics
- No execution side effects
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from starlette.middleware.base import (
    BaseHTTPMiddleware,
    RequestResponseEndpoint,
)
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

_API_AUDIT_LOG = Path("artifacts/api_request_audit.jsonl")
_REQUEST_ID_HEADER = "X-Request-ID"
# Default maximum body size: 64 KiB.  Override via RequestGovernanceMiddleware
# constructor or APP_MAX_REQUEST_BODY_BYTES setting.
_DEFAULT_MAX_BODY_BYTES = 65_536


@dataclass(frozen=True)
class APIErrorResponse:
    """Immutable structured error response."""

    error: str
    detail: str
    request_id: str
    status_code: int
    execution_enabled: bool = False
    write_back_allowed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.error,
            "detail": self.detail,
            "request_id": self.request_id,
            "status_code": self.status_code,
            "execution_enabled": self.execution_enabled,
            "write_back_allowed": self.write_back_allowed,
        }


def _extract_client_ip(request: Request) -> str:
    """Return the best-effort client IP address from the request."""
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client is not None:
        return request.client.host
    return "unknown"


class RequestGovernanceMiddleware(BaseHTTPMiddleware):
    """Middleware for request-ID propagation, body-size enforcement, and audit logging.

    - Generates X-Request-ID if not provided
    - Adds X-Request-ID to response headers
    - Rejects oversized request bodies with HTTP 413 before routing
    - Logs request method, path, status, duration, and client_ip to JSONL
    - Fail-closed on audit errors (logs warning, doesn't crash)
    """

    def __init__(
        self,
        app: Any,
        *,
        audit_log_path: str | Path = _API_AUDIT_LOG,
        max_body_bytes: int = _DEFAULT_MAX_BODY_BYTES,
    ) -> None:
        super().__init__(app)
        self._audit_path = Path(audit_log_path)
        self._audit_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        self._max_body_bytes = max_body_bytes

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        # Generate or reuse request ID
        request_id = request.headers.get(_REQUEST_ID_HEADER) or f"req_{uuid.uuid4().hex[:12]}"
        request.state.request_id = request_id
        client_ip = _extract_client_ip(request)

        # Enforce body-size limit for methods that carry a body
        if request.method in ("POST", "PUT", "PATCH"):
            content_length_str = request.headers.get("Content-Length")
            if content_length_str is not None:
                try:
                    content_length = int(content_length_str)
                except ValueError:
                    content_length = 0
                if content_length > self._max_body_bytes:
                    error_response = JSONResponse(
                        status_code=413,
                        content={
                            "error": {
                                "code": "request_body_too_large",
                                "message": (
                                    f"Request body exceeds maximum allowed size "
                                    f"({self._max_body_bytes} bytes)"
                                ),
                                "request_id": request_id,
                            },
                            "execution_enabled": False,
                            "write_back_allowed": False,
                        },
                        headers={_REQUEST_ID_HEADER: request_id},
                    )
                    self._write_audit(
                        request_id=request_id,
                        method=request.method,
                        path=str(request.url.path),
                        status_code=413,
                        duration_ms=0.0,
                        client_ip=client_ip,
                    )
                    return error_response

        start = time.monotonic()
        response = await call_next(request)
        duration_ms = round(
            (time.monotonic() - start) * 1000,
            2,
        )

        # Propagate request ID in response
        response.headers[_REQUEST_ID_HEADER] = request_id

        # Audit log
        self._write_audit(
            request_id=request_id,
            method=request.method,
            path=str(request.url.path),
            status_code=response.status_code,
            duration_ms=duration_ms,
            client_ip=client_ip,
        )

        return response

    def _write_audit(
        self,
        *,
        request_id: str,
        method: str,
        path: str,
        status_code: int,
        duration_ms: float,
        client_ip: str = "unknown",
    ) -> None:
        record = {
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "request_id": request_id,
            "method": method,
            "path": path,
            "status_code": status_code,
            "duration_ms": duration_ms,
            "client_ip": client_ip,
        }
        try:
            with self._audit_path.open(
                "a",
                encoding="utf-8",
            ) as fh:
                fh.write(json.dumps(record) + "\n")
        except OSError as exc:
            logger.warning(
                "[API AUDIT] Write failed: %s",
                exc,
            )
