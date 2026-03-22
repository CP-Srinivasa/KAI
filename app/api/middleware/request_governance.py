"""API request governance middleware.

Provides:
- Request-ID generation and propagation (X-Request-ID)
- API request audit logging (append-only JSONL)
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
from starlette.responses import Response

logger = logging.getLogger(__name__)

_API_AUDIT_LOG = Path("artifacts/api_request_audit.jsonl")
_REQUEST_ID_HEADER = "X-Request-ID"


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


class RequestGovernanceMiddleware(BaseHTTPMiddleware):
    """Middleware for request-ID propagation and audit logging.

    - Generates X-Request-ID if not provided
    - Adds X-Request-ID to response headers
    - Logs request method, path, status, and duration to JSONL
    - Fail-closed on audit errors (logs warning, doesn't crash)
    """

    def __init__(
        self,
        app: Any,
        *,
        audit_log_path: str | Path = _API_AUDIT_LOG,
    ) -> None:
        super().__init__(app)
        self._audit_path = Path(audit_log_path)
        self._audit_path.parent.mkdir(
            parents=True, exist_ok=True,
        )

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        # Generate or reuse request ID
        request_id = (
            request.headers.get(_REQUEST_ID_HEADER)
            or f"req_{uuid.uuid4().hex[:12]}"
        )
        request.state.request_id = request_id

        start = time.monotonic()
        response = await call_next(request)
        duration_ms = round(
            (time.monotonic() - start) * 1000, 2,
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
    ) -> None:
        record = {
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "request_id": request_id,
            "method": method,
            "path": path,
            "status_code": status_code,
            "duration_ms": duration_ms,
        }
        try:
            with self._audit_path.open(
                "a", encoding="utf-8",
            ) as fh:
                fh.write(json.dumps(record) + "\n")
        except OSError as exc:
            logger.warning(
                "[API AUDIT] Write failed: %s", exc,
            )
