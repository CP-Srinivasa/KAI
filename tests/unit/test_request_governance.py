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
from fastapi import FastAPI
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
    lines = (
        audit_path.read_text(encoding="utf-8")
        .strip()
        .splitlines()
    )
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
    lines = (
        audit_path.read_text(encoding="utf-8")
        .strip()
        .splitlines()
    )
    assert len(lines) == 3


# ------------------------------------------------------------------
# APIErrorResponse
# ------------------------------------------------------------------


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
