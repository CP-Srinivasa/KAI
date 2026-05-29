"""Tests for API request governance middleware.

Covers:
- X-Request-ID generation and propagation
- Request-ID reuse from client header
- API audit logging (JSONL)
- APIErrorResponse model immutability
- Fail-closed on audit errors
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api.middleware.request_governance import (
    APIErrorResponse,
    RequestGovernanceMiddleware,
)


def _app_with_middleware(
    tmp_path: Path,
) -> tuple[FastAPI, Path]:
    app = FastAPI()
    audit_path = tmp_path / "api_audit.jsonl"

    app.add_middleware(
        RequestGovernanceMiddleware,
        audit_log_path=str(audit_path),
    )

    @app.get("/test")
    async def _test_endpoint() -> dict[str, str]:
        return {"status": "ok"}

    return app, audit_path


# ------------------------------------------------------------------
# X-Request-ID
# ------------------------------------------------------------------


def test_generates_request_id(tmp_path: Path) -> None:
    app, _ = _app_with_middleware(tmp_path)
    c = TestClient(app)
    r = c.get("/test")
    assert r.status_code == 200
    rid = r.headers.get("X-Request-ID")
    assert rid is not None
    assert rid.startswith("req_")
    assert len(rid) == 16  # "req_" + 12 hex


def test_reuses_client_request_id(tmp_path: Path) -> None:
    app, _ = _app_with_middleware(tmp_path)
    c = TestClient(app)
    r = c.get(
        "/test",
        headers={"X-Request-ID": "custom-id-123"},
    )
    assert r.headers["X-Request-ID"] == "custom-id-123"


def test_unique_ids_per_request(tmp_path: Path) -> None:
    app, _ = _app_with_middleware(tmp_path)
    c = TestClient(app)
    ids = set()
    for _ in range(5):
        r = c.get("/test")
        ids.add(r.headers["X-Request-ID"])
    assert len(ids) == 5


# ------------------------------------------------------------------
# Audit logging
# ------------------------------------------------------------------


def test_audit_log_written(tmp_path: Path) -> None:
    app, audit_path = _app_with_middleware(tmp_path)
    c = TestClient(app)
    c.get("/test")
    assert audit_path.exists()
    lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["method"] == "GET"
    assert record["path"] == "/test"
    assert record["status_code"] == 200
    assert "request_id" in record
    assert "duration_ms" in record
    assert "timestamp_utc" in record


def test_audit_log_multiple_requests(
    tmp_path: Path,
) -> None:
    app, audit_path = _app_with_middleware(tmp_path)
    c = TestClient(app)
    c.get("/test")
    c.get("/test")
    c.get("/nonexistent")
    lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3


# ------------------------------------------------------------------
# APIErrorResponse
# ------------------------------------------------------------------


# ------------------------------------------------------------------
# Body-size limit (AUDIT-A6): hard cap, not just Content-Length trust
# ------------------------------------------------------------------


def _app_with_body_cap(tmp_path: Path, max_bytes: int = 100) -> TestClient:
    from starlette.responses import Response

    app = FastAPI()
    app.add_middleware(
        RequestGovernanceMiddleware,
        audit_log_path=str(tmp_path / "api_audit.jsonl"),
        max_body_bytes=max_bytes,
    )

    @app.post("/echo")
    async def _echo(request: Request) -> Response:
        body = await request.body()  # must see the cached/replayed bytes
        return Response(content=body, media_type="application/octet-stream")

    return TestClient(app)


def test_body_under_limit_passes_and_is_intact(tmp_path: Path) -> None:
    c = _app_with_body_cap(tmp_path, max_bytes=100)
    payload = b"x" * 50
    r = c.post("/echo", content=payload)
    assert r.status_code == 200
    assert r.content == payload  # downstream handler saw the full body


def test_oversized_content_length_rejected(tmp_path: Path) -> None:
    c = _app_with_body_cap(tmp_path, max_bytes=100)
    r = c.post("/echo", content=b"x" * 500)  # httpx sets honest Content-Length
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "request_body_too_large"


def test_chunked_oversized_rejected_without_content_length(tmp_path: Path) -> None:
    """A streamed body has no Content-Length (chunked). The hard byte cap must
    still reject it — the Content-Length fast path alone would let it through."""
    c = _app_with_body_cap(tmp_path, max_bytes=100)

    def _gen() -> "object":
        for _ in range(10):
            yield b"x" * 50  # 500 bytes total, sent chunked → no Content-Length

    r = c.post("/echo", content=_gen())
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "request_body_too_large"


def test_error_response_frozen() -> None:
    err = APIErrorResponse(
        error="auth_failed",
        detail="Missing token",
        request_id="req_abc",
        status_code=401,
    )
    with pytest.raises(AttributeError):
        err.error = "changed"  # type: ignore[misc]


def test_error_response_to_dict() -> None:
    err = APIErrorResponse(
        error="auth_failed",
        detail="Missing token",
        request_id="req_abc",
        status_code=401,
    )
    d = err.to_dict()
    assert d["error"] == "auth_failed"
    assert d["request_id"] == "req_abc"
    assert d["execution_enabled"] is False
    assert d["write_back_allowed"] is False


def test_error_response_defaults() -> None:
    err = APIErrorResponse(
        error="e",
        detail="d",
        request_id="r",
        status_code=500,
    )
    assert err.execution_enabled is False
    assert err.write_back_allowed is False
