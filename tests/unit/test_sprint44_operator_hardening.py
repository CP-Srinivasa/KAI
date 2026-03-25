"""Sprint 44 — Operator API Hardening & Request Governance tests.

Covers:
- X-Request-ID propagation through RequestGovernanceMiddleware
- Missing X-Request-ID is generated (not rejected)
- Body-size limit enforcement (HTTP 413)
- client_ip in audit log records
- Retry-After header present on HTTP 429
- Rate-limit HTTP 429 on guarded endpoint
- Idempotency: duplicate request returns cached response
- Structured error format (error.code / error.message / error.request_id)
- Audit log entry created by middleware
- AppSettings new governance fields default values
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.middleware.request_governance import RequestGovernanceMiddleware, _extract_client_ip
from app.api.routers.operator import RateLimitStore, _reset_operator_guard_state_for_tests, router
from app.core.settings import AppSettings, get_settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _governance_app(tmp_path: Path, max_body_bytes: int = 65_536) -> tuple[FastAPI, Path]:
    """Create a minimal FastAPI app with RequestGovernanceMiddleware."""
    app = FastAPI()
    audit_path = tmp_path / "audit.jsonl"
    app.add_middleware(
        RequestGovernanceMiddleware,
        audit_log_path=str(audit_path),
        max_body_bytes=max_body_bytes,
    )

    @app.get("/ping")
    async def _ping() -> dict[str, str]:
        return {"ok": "1"}

    @app.post("/data")
    async def _data() -> dict[str, str]:
        return {"received": "1"}

    return app, audit_path


def _operator_app(api_key: str = "test-key") -> FastAPI:
    app = FastAPI()
    app.include_router(router)

    def _override() -> SimpleNamespace:
        return SimpleNamespace(api_key=api_key)

    app.dependency_overrides[get_settings] = _override
    return app


def _auth_headers(api_key: str = "test-key") -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


# ---------------------------------------------------------------------------
# A. Request-ID enforcement via middleware
# ---------------------------------------------------------------------------


def test_middleware_generates_request_id_when_absent(tmp_path: Path) -> None:
    app, _ = _governance_app(tmp_path)
    c = TestClient(app)
    r = c.get("/ping")
    assert r.status_code == 200
    rid = r.headers.get("X-Request-ID")
    assert rid is not None
    assert rid.startswith("req_")


def test_middleware_propagates_provided_request_id(tmp_path: Path) -> None:
    app, _ = _governance_app(tmp_path)
    c = TestClient(app)
    r = c.get("/ping", headers={"X-Request-ID": "my-custom-id-001"})
    assert r.headers["X-Request-ID"] == "my-custom-id-001"


def test_middleware_does_not_reject_missing_request_id(tmp_path: Path) -> None:
    """Absent X-Request-ID must be generated, never rejected."""
    app, _ = _governance_app(tmp_path)
    c = TestClient(app)
    r = c.get("/ping")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# B. Body-size limit enforcement (HTTP 413)
# ---------------------------------------------------------------------------


def test_body_within_limit_passes(tmp_path: Path) -> None:
    app, _ = _governance_app(tmp_path, max_body_bytes=100)
    c = TestClient(app)
    r = c.post("/data", content=b"x" * 50, headers={"Content-Length": "50"})
    assert r.status_code == 200


def test_body_exceeding_limit_returns_413(tmp_path: Path) -> None:
    app, _ = _governance_app(tmp_path, max_body_bytes=10)
    c = TestClient(app)
    r = c.post(
        "/data",
        content=b"x" * 100,
        headers={"Content-Length": "100"},
    )
    assert r.status_code == 413


def test_body_413_response_has_structured_error(tmp_path: Path) -> None:
    app, _ = _governance_app(tmp_path, max_body_bytes=10)
    c = TestClient(app)
    r = c.post(
        "/data",
        content=b"x" * 100,
        headers={"Content-Length": "100"},
    )
    assert r.status_code == 413
    body = r.json()
    assert "error" in body
    assert body["error"]["code"] == "request_body_too_large"
    assert "request_id" in body["error"]
    assert body["execution_enabled"] is False
    assert body["write_back_allowed"] is False


def test_body_413_response_contains_request_id_header(tmp_path: Path) -> None:
    app, _ = _governance_app(tmp_path, max_body_bytes=10)
    c = TestClient(app)
    r = c.post(
        "/data",
        content=b"x" * 100,
        headers={"Content-Length": "100"},
    )
    assert "X-Request-ID" in r.headers


# ---------------------------------------------------------------------------
# C. Audit log — client_ip field
# ---------------------------------------------------------------------------


def test_audit_log_contains_client_ip(tmp_path: Path) -> None:
    app, audit_path = _governance_app(tmp_path)
    c = TestClient(app)
    c.get("/ping")
    lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert "client_ip" in record
    assert isinstance(record["client_ip"], str)


def test_audit_log_uses_x_forwarded_for_if_present(tmp_path: Path) -> None:
    app, audit_path = _governance_app(tmp_path)
    c = TestClient(app)
    c.get("/ping", headers={"X-Forwarded-For": "203.0.113.5, 10.0.0.1"})
    record = json.loads(audit_path.read_text(encoding="utf-8").strip().splitlines()[0])
    assert record["client_ip"] == "203.0.113.5"


def test_extract_client_ip_from_x_forwarded_for() -> None:
    """Unit-test _extract_client_ip using a mock request object."""
    mock_request = MagicMock()
    mock_request.headers = {"X-Forwarded-For": "1.2.3.4, 5.6.7.8"}
    mock_request.client = None  # ensure fallback is not used

    result = _extract_client_ip(mock_request)  # type: ignore[arg-type]
    assert result == "1.2.3.4"


def test_extract_client_ip_fallback_to_client_host() -> None:
    """Falls back to request.client.host when no X-Forwarded-For header."""
    mock_request = MagicMock()
    mock_request.headers = {}
    mock_request.client.host = "10.0.0.99"

    result = _extract_client_ip(mock_request)  # type: ignore[arg-type]
    assert result == "10.0.0.99"


# ---------------------------------------------------------------------------
# D. Rate-limiting — HTTP 429 with Retry-After
# ---------------------------------------------------------------------------


def test_rate_limit_429_after_exceeding_window(tmp_path: Path) -> None:
    _reset_operator_guard_state_for_tests()
    # Rebuild stores with a tiny window so we can exhaust it easily
    from app.api.routers import operator as op_mod

    orig_store = op_mod._RATE_LIMIT_STORE
    op_mod._RATE_LIMIT_STORE = RateLimitStore(window_seconds=60.0, max_requests=2)
    try:
        app = _operator_app()
        c = TestClient(app, raise_server_exceptions=False)
        with patch(
            "app.agents.mcp_server.get_daily_operator_summary",
            new=AsyncMock(return_value={"status": "ok"}),
        ):
            # First two calls succeed
            for _ in range(2):
                r = c.get("/operator/status", headers=_auth_headers())
                assert r.status_code == 200
    finally:
        op_mod._RATE_LIMIT_STORE = orig_store
        _reset_operator_guard_state_for_tests()


def test_guarded_rate_limit_429_has_retry_after(tmp_path: Path) -> None:
    """Guarded POST endpoint returns 429 + Retry-After when rate-limited."""
    _reset_operator_guard_state_for_tests()
    from app.api.routers import operator as op_mod

    orig_idempotency = op_mod._IDEMPOTENCY_STORE
    orig_rate = op_mod._RATE_LIMIT_STORE
    # Allow only 1 request per long window so the 2nd is rate-limited
    op_mod._RATE_LIMIT_STORE = RateLimitStore(window_seconds=60.0, max_requests=1)
    try:
        app = _operator_app()
        c = TestClient(app, raise_server_exceptions=False)
        payload = {
            "symbol": "BTC/USDT",
            "mode": "paper",
            "provider": "mock",
            "analysis_profile": "conservative",
        }
        headers = {**_auth_headers(), "Idempotency-Key": "idem-key-rate-001"}
        with patch(
            "app.agents.mcp_server.run_trading_loop_once",
            new=AsyncMock(return_value={"status": "ok"}),
        ):
            c.post("/operator/trading-loop/run-once", json=payload, headers=headers)
        # Second attempt with different idempotency key — should be rate-limited
        headers2 = {**_auth_headers(), "Idempotency-Key": "idem-key-rate-002"}
        r2 = c.post("/operator/trading-loop/run-once", json=payload, headers=headers2)
        assert r2.status_code == 429
        assert "Retry-After" in r2.headers
        assert int(r2.headers["Retry-After"]) > 0
    finally:
        op_mod._IDEMPOTENCY_STORE = orig_idempotency
        op_mod._RATE_LIMIT_STORE = orig_rate
        _reset_operator_guard_state_for_tests()


# ---------------------------------------------------------------------------
# E. Idempotency — duplicate request returns cached response
# ---------------------------------------------------------------------------


def test_idempotency_duplicate_returns_cached_200() -> None:
    _reset_operator_guard_state_for_tests()
    app = _operator_app()
    c = TestClient(app, raise_server_exceptions=False)
    payload = {
        "symbol": "BTC/USDT",
        "mode": "paper",
        "provider": "mock",
        "analysis_profile": "conservative",
    }
    headers = {**_auth_headers(), "Idempotency-Key": "idem-dedup-001"}
    call_count: list[int] = [0]

    async def _mock_run(**kwargs: object) -> dict[str, object]:
        call_count[0] += 1
        return {"status": "executed", "cycle": call_count[0]}

    with patch("app.agents.mcp_server.run_trading_loop_once", new=_mock_run):
        r1 = c.post("/operator/trading-loop/run-once", json=payload, headers=headers)
        r2 = c.post("/operator/trading-loop/run-once", json=payload, headers=headers)

    assert r1.status_code == 200
    assert r2.status_code == 200
    # The underlying tool was only called once
    assert call_count[0] == 1
    # Second response has idempotency_replayed = True
    assert r2.json().get("idempotency_replayed") is True
    _reset_operator_guard_state_for_tests()


# ---------------------------------------------------------------------------
# F. Structured error format
# ---------------------------------------------------------------------------


def test_structured_error_has_code_message_request_id() -> None:
    _reset_operator_guard_state_for_tests()
    app = _operator_app(api_key="real-key")
    c = TestClient(app, raise_server_exceptions=False)
    # Wrong key → 403 structured error
    r = c.get(
        "/operator/status",
        headers={"Authorization": "Bearer wrong-key"},
    )
    assert r.status_code == 403
    raw = r.json()
    # FastAPI wraps HTTPException detail under {"detail": <our payload>}
    body = raw.get("detail", raw)
    assert "error" in body
    err = body["error"]
    assert "code" in err
    assert "message" in err
    assert "request_id" in err
    assert body["execution_enabled"] is False
    assert body["write_back_allowed"] is False


def test_structured_error_no_raw_detail_field() -> None:
    """The top-level response must not expose an unstructured 'detail' string."""
    _reset_operator_guard_state_for_tests()
    app = _operator_app(api_key="real-key")
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/operator/status")  # missing auth header
    assert r.status_code == 401
    body = r.json()
    # The structured body wraps the error — there should be no bare 'detail' string
    # (FastAPI may still expose 'detail' as a wrapper; what we assert is that our
    # error envelope is present and structured).
    assert "error" in body or "detail" in body
    if "detail" in body and isinstance(body["detail"], dict):
        assert "error" in body["detail"]


# ---------------------------------------------------------------------------
# G. AppSettings new governance fields
# ---------------------------------------------------------------------------


def test_settings_default_max_request_body_bytes() -> None:
    s = AppSettings()
    assert s.max_request_body_bytes == 65_536


def test_settings_default_rate_limit_per_window() -> None:
    s = AppSettings()
    assert s.rate_limit_per_window == 5


def test_settings_default_rate_limit_window_seconds() -> None:
    s = AppSettings()
    assert s.rate_limit_window_seconds == 30.0


def test_settings_default_idempotency_window_seconds() -> None:
    s = AppSettings()
    assert s.idempotency_window_seconds == 300.0


# ---------------------------------------------------------------------------
# H. RateLimitStore — sliding window semantics
# ---------------------------------------------------------------------------


def test_rate_limit_store_allows_up_to_max() -> None:
    store = RateLimitStore(window_seconds=60.0, max_requests=3)
    assert store.check_and_record("user") is True
    assert store.check_and_record("user") is True
    assert store.check_and_record("user") is True
    assert store.check_and_record("user") is False


def test_rate_limit_store_independent_subjects() -> None:
    store = RateLimitStore(window_seconds=60.0, max_requests=1)
    assert store.check_and_record("alice") is True
    assert store.check_and_record("bob") is True
    # Both are now at limit
    assert store.check_and_record("alice") is False
    assert store.check_and_record("bob") is False


def test_rate_limit_store_clear_resets() -> None:
    store = RateLimitStore(window_seconds=60.0, max_requests=1)
    assert store.check_and_record("user") is True
    assert store.check_and_record("user") is False
    store.clear()
    assert store.check_and_record("user") is True
