from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import operator as operator_router
from app.core.settings import AppSettings


def _set_operator_api_key(app: FastAPI, value: str) -> None:
    settings = AppSettings()
    settings.api_key = value
    app.dependency_overrides[operator_router.get_settings] = lambda: settings


def _auth_headers(
    *,
    token: str = "operator-token",
    request_id: str | None = None,
    correlation_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if request_id is not None:
        headers["X-Request-ID"] = request_id
    if correlation_id is not None:
        headers["X-Correlation-ID"] = correlation_id
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def _assert_error_payload(
    response,
    *,
    status_code: int,
    error_code: str,
) -> dict[str, Any]:
    assert response.status_code == status_code
    body = response.json()
    assert isinstance(body.get("detail"), dict)
    detail = body["detail"]
    assert detail["error"]["code"] == error_code
    assert detail["error"]["request_id"]
    assert detail["error"]["correlation_id"]
    assert detail["execution_enabled"] is False
    assert detail["write_back_allowed"] is False
    assert response.headers["X-Request-ID"]
    assert response.headers["X-Correlation-ID"]
    return detail


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    operator_router._reset_operator_guard_state_for_tests()
    monkeypatch.setattr(operator_router, "_WORKSPACE_ROOT", tmp_path.resolve())

    test_app = FastAPI()
    test_app.include_router(operator_router.router)
    with TestClient(test_app) as test_client:
        yield test_client

    test_app.dependency_overrides.clear()
    operator_router._reset_operator_guard_state_for_tests()


def test_operator_endpoints_fail_closed_when_api_key_missing(client: TestClient) -> None:
    _set_operator_api_key(client.app, "")
    response = client.get("/operator/readiness")
    detail = _assert_error_payload(
        response,
        status_code=503,
        error_code="operator_api_disabled",
    )
    assert "fail-closed" in detail["error"]["message"]


def test_operator_endpoints_require_bearer_header(client: TestClient) -> None:
    _set_operator_api_key(client.app, "operator-token")
    response = client.get("/operator/readiness")
    detail = _assert_error_payload(
        response,
        status_code=401,
        error_code="missing_authorization_header",
    )
    assert detail["error"]["message"] == "Missing Authorization header"
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_operator_endpoints_reject_invalid_bearer_token(client: TestClient) -> None:
    _set_operator_api_key(client.app, "operator-token")
    response = client.get(
        "/operator/readiness",
        headers={"Authorization": "Bearer wrong-token"},
    )
    detail = _assert_error_payload(
        response,
        status_code=403,
        error_code="invalid_api_key",
    )
    assert detail["error"]["message"] == "Invalid API key"


@pytest.mark.parametrize(
    ("path", "mcp_name", "payload"),
    [
        (
            "/operator/status",
            "get_operational_readiness_summary",
            {
                "report_type": "operational_readiness_summary",
                "readiness_status": "warning",
                "execution_enabled": False,
                "write_back_allowed": False,
            },
        ),
        (
            "/operator/readiness",
            "get_operational_readiness_summary",
            {
                "report_type": "operational_readiness_summary",
                "readiness_status": "ok",
                "execution_enabled": False,
                "write_back_allowed": False,
            },
        ),
        (
            "/operator/decision-pack",
            "get_decision_pack_summary",
            {
                "report_type": "operator_decision_pack",
                "overall_status": "clear",
                "execution_enabled": False,
                "write_back_allowed": False,
            },
        ),
        (
            "/operator/daily-summary",
            "get_daily_operator_summary",
            {
                "report_type": "daily_operator_summary",
                "readiness_status": "warning",
                "cycle_count_today": 2,
                "position_count": 1,
                "total_exposure_pct": 18.5,
                "decision_pack_status": "warning",
                "open_incidents": 1,
                "execution_enabled": False,
                "write_back_allowed": False,
            },
        ),
        (
            "/operator/review-journal",
            "get_review_journal_summary",
            {
                "report_type": "review_journal_summary",
                "journal_status": "open",
                "total_count": 2,
                "open_count": 1,
                "resolved_count": 1,
                "execution_enabled": False,
                "write_back_allowed": False,
            },
        ),
        (
            "/operator/resolution-summary",
            "get_resolution_summary",
            {
                "report_type": "review_resolution_summary",
                "total_sources": 1,
                "open_count": 0,
                "resolved_count": 1,
                "execution_enabled": False,
                "write_back_allowed": False,
            },
        ),
        (
            "/operator/alert-audit",
            "get_alert_audit_summary",
            {
                "report_type": "alert_audit_summary",
                "total_count": 0,
                "digest_count": 0,
                "execution_enabled": False,
                "write_back_allowed": False,
            },
        ),
        (
            "/operator/portfolio-snapshot",
            "get_paper_portfolio_snapshot",
            {
                "report_type": "paper_portfolio_snapshot",
                "position_count": 0,
                "execution_enabled": False,
                "write_back_allowed": False,
            },
        ),
        (
            "/operator/exposure-summary",
            "get_paper_exposure_summary",
            {
                "report_type": "paper_exposure_summary",
                "mark_to_market_status": "ok",
                "execution_enabled": False,
                "write_back_allowed": False,
            },
        ),
        (
            "/operator/trading-loop/status",
            "get_trading_loop_status",
            {
                "report_type": "trading_loop_status_summary",
                "mode": "paper",
                "run_once_allowed": True,
                "execution_enabled": False,
                "write_back_allowed": False,
            },
        ),
        (
            "/operator/trading-loop/recent-cycles",
            "get_recent_trading_cycles",
            {
                "report_type": "trading_loop_recent_cycles",
                "total_cycles": 0,
                "recent_cycles": [],
                "execution_enabled": False,
                "write_back_allowed": False,
            },
        ),
    ],
)
def test_operator_read_endpoints_passthrough_canonical_payloads(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    mcp_name: str,
    payload: dict[str, Any],
) -> None:
    _set_operator_api_key(client.app, "operator-token")

    async def fake_surface(**_kwargs: object) -> dict[str, Any]:
        return payload

    monkeypatch.setattr(operator_router.mcp_server, mcp_name, fake_surface)

    response = client.get(path, headers=_auth_headers())
    assert response.status_code == 200
    assert response.json() == payload
    assert response.headers["X-Request-ID"]
    assert response.headers["X-Correlation-ID"]
    assert response.json()["execution_enabled"] is False
    assert response.json()["write_back_allowed"] is False
    assert "broker" not in json.dumps(response.json()).lower()


def test_operator_request_id_generated_and_forwarded(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_operator_api_key(client.app, "operator-token")

    async def fake_surface(**_kwargs: object) -> dict[str, object]:
        return {
            "report_type": "operational_readiness",
            "execution_enabled": False,
            "write_back_allowed": False,
        }

    monkeypatch.setattr(
        operator_router.mcp_server,
        "get_operational_readiness_summary",
        fake_surface,
    )

    response = client.get("/operator/readiness", headers=_auth_headers())
    assert response.status_code == 200
    assert response.headers["X-Request-ID"].startswith("req_")
    assert response.headers["X-Correlation-ID"] == response.headers["X-Request-ID"]


def test_operator_request_id_and_correlation_id_passthrough(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_operator_api_key(client.app, "operator-token")

    async def fake_surface(**_kwargs: object) -> dict[str, object]:
        return {
            "report_type": "operational_readiness",
            "execution_enabled": False,
            "write_back_allowed": False,
        }

    monkeypatch.setattr(
        operator_router.mcp_server,
        "get_operational_readiness_summary",
        fake_surface,
    )

    response = client.get(
        "/operator/readiness",
        headers=_auth_headers(request_id="req-fixed", correlation_id="corr-fixed"),
    )
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "req-fixed"
    assert response.headers["X-Correlation-ID"] == "corr-fixed"


def test_operator_read_error_payload_is_consistent(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_operator_api_key(client.app, "operator-token")

    async def failing_surface(**_kwargs: object) -> dict[str, object]:
        raise RuntimeError("broken")

    monkeypatch.setattr(
        operator_router.mcp_server,
        "get_operational_readiness_summary",
        failing_surface,
    )

    response = client.get("/operator/readiness", headers=_auth_headers())
    detail = _assert_error_payload(
        response,
        status_code=503,
        error_code="readiness_unavailable",
    )
    assert "RuntimeError" in detail["error"]["message"]


@pytest.mark.parametrize(
    ("path", "mcp_name", "error_code"),
    [
        (
            "/operator/review-journal",
            "get_review_journal_summary",
            "review_journal_unavailable",
        ),
        (
            "/operator/resolution-summary",
            "get_resolution_summary",
            "resolution_summary_unavailable",
        ),
        (
            "/operator/alert-audit",
            "get_alert_audit_summary",
            "alert_audit_unavailable",
        ),
    ],
)
def test_operator_journal_read_endpoints_use_consistent_error_shape(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    mcp_name: str,
    error_code: str,
) -> None:
    _set_operator_api_key(client.app, "operator-token")

    async def failing_surface(**_kwargs: object) -> dict[str, object]:
        raise RuntimeError("broken")

    monkeypatch.setattr(
        operator_router.mcp_server,
        mcp_name,
        failing_surface,
    )

    response = client.get(path, headers=_auth_headers())
    detail = _assert_error_payload(
        response,
        status_code=503,
        error_code=error_code,
    )
    assert "RuntimeError" in detail["error"]["message"]


def test_operator_run_once_requires_idempotency_key(client: TestClient) -> None:
    _set_operator_api_key(client.app, "operator-token")

    response = client.post(
        "/operator/trading-loop/run-once",
        headers=_auth_headers(),
        json={"symbol": "BTC/USDT", "mode": "paper", "provider": "mock"},
    )
    _assert_error_payload(
        response,
        status_code=400,
        error_code="missing_idempotency_key",
    )


@pytest.mark.parametrize("mode", ["paper", "shadow"])
def test_operator_run_once_guarded_endpoint_accepts_only_controlled_modes(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    _set_operator_api_key(client.app, "operator-token")

    async def fake_run_once(**kwargs: object) -> dict[str, object]:
        return {
            "status": "cycle_completed",
            "mode": kwargs["mode"],
            "provider": kwargs["provider"],
            "analysis_profile": kwargs["analysis_profile"],
            "cycle": {
                "status": "no_signal",
                "order_created": False,
                "fill_simulated": False,
            },
            "auto_loop_enabled": False,
            "execution_enabled": False,
            "write_back_allowed": False,
        }

    monkeypatch.setattr(operator_router.mcp_server, "run_trading_loop_once", fake_run_once)

    response = client.post(
        "/operator/trading-loop/run-once",
        headers=_auth_headers(idempotency_key=f"idem-{mode}"),
        json={"symbol": "BTC/USDT", "mode": mode, "provider": "mock"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "cycle_completed"
    assert payload["mode"] == mode
    assert payload["cycle"]["status"] == "no_signal"
    assert payload["auto_loop_enabled"] is False
    assert payload["execution_enabled"] is False
    assert payload["write_back_allowed"] is False
    assert payload["cycle"]["order_created"] is False
    assert payload["cycle"]["fill_simulated"] is False
    assert payload["idempotency_replayed"] is False


def test_operator_run_once_live_mode_is_fail_closed_with_consistent_error(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_operator_api_key(client.app, "operator-token")

    async def fake_run_once(**_kwargs: object) -> dict[str, object]:
        raise ValueError(
            "trading_loop_run_once blocked: mode=live is not allowed (allowed: paper, shadow)"
        )

    monkeypatch.setattr(operator_router.mcp_server, "run_trading_loop_once", fake_run_once)

    response = client.post(
        "/operator/trading-loop/run-once",
        headers=_auth_headers(idempotency_key="idem-live"),
        json={"symbol": "BTC/USDT", "mode": "live", "provider": "mock"},
    )
    detail = _assert_error_payload(
        response,
        status_code=400,
        error_code="guarded_request_rejected",
    )
    assert "allowed: paper, shadow" in detail["error"]["message"]


def test_operator_run_once_idempotency_replays_same_payload_without_second_call(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_operator_api_key(client.app, "operator-token")
    call_count = 0

    async def fake_run_once(**kwargs: object) -> dict[str, object]:
        nonlocal call_count
        call_count += 1
        return {
            "status": "cycle_completed",
            "mode": kwargs["mode"],
            "provider": kwargs["provider"],
            "analysis_profile": kwargs["analysis_profile"],
            "cycle": {"status": "no_signal", "order_created": False, "fill_simulated": False},
            "auto_loop_enabled": False,
            "execution_enabled": False,
            "write_back_allowed": False,
        }

    monkeypatch.setattr(operator_router.mcp_server, "run_trading_loop_once", fake_run_once)

    body = {"symbol": "BTC/USDT", "mode": "paper", "provider": "mock"}
    first = client.post(
        "/operator/trading-loop/run-once",
        headers=_auth_headers(idempotency_key="idem-replay"),
        json=body,
    )
    second = client.post(
        "/operator/trading-loop/run-once",
        headers=_auth_headers(idempotency_key="idem-replay"),
        json=body,
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert call_count == 1
    assert first.json()["idempotency_replayed"] is False
    assert second.json()["idempotency_replayed"] is True

    audit_path = tmp_path / "artifacts" / "operator_api_guarded_audit.jsonl"
    assert audit_path.exists()
    rows = [
        json.loads(line)
        for line in audit_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 2
    assert rows[0]["outcome"] == "accepted"
    assert rows[1]["outcome"] == "idempotency_replay"


def test_operator_run_once_idempotency_conflict_rejected(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_operator_api_key(client.app, "operator-token")

    async def fake_run_once(**kwargs: object) -> dict[str, object]:
        return {
            "status": "cycle_completed",
            "mode": kwargs["mode"],
            "provider": kwargs["provider"],
            "analysis_profile": kwargs["analysis_profile"],
            "cycle": {"status": "no_signal", "order_created": False, "fill_simulated": False},
            "auto_loop_enabled": False,
            "execution_enabled": False,
            "write_back_allowed": False,
        }

    monkeypatch.setattr(operator_router.mcp_server, "run_trading_loop_once", fake_run_once)

    first = client.post(
        "/operator/trading-loop/run-once",
        headers=_auth_headers(idempotency_key="idem-conflict"),
        json={"symbol": "BTC/USDT", "mode": "paper", "provider": "mock"},
    )
    conflict = client.post(
        "/operator/trading-loop/run-once",
        headers=_auth_headers(idempotency_key="idem-conflict"),
        json={"symbol": "ETH/USDT", "mode": "paper", "provider": "mock"},
    )

    assert first.status_code == 200
    _assert_error_payload(
        conflict,
        status_code=409,
        error_code="idempotency_key_conflict",
    )


def test_operator_run_once_light_rate_limit_blocks_after_threshold(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_operator_api_key(client.app, "operator-token")
    # Replace the rate-limit store with a tight 1-request limit for this test.
    # Patching the module-level constants no longer suffices since RateLimitStore
    # is initialised once at import time — replace the store instance instead.
    tight_store = operator_router.RateLimitStore(window_seconds=3600.0, max_requests=1)
    monkeypatch.setattr(operator_router, "_RATE_LIMIT_STORE", tight_store)

    async def fake_run_once(**kwargs: object) -> dict[str, object]:
        return {
            "status": "cycle_completed",
            "mode": kwargs["mode"],
            "provider": kwargs["provider"],
            "analysis_profile": kwargs["analysis_profile"],
            "cycle": {"status": "no_signal", "order_created": False, "fill_simulated": False},
            "auto_loop_enabled": False,
            "execution_enabled": False,
            "write_back_allowed": False,
        }

    monkeypatch.setattr(operator_router.mcp_server, "run_trading_loop_once", fake_run_once)

    first = client.post(
        "/operator/trading-loop/run-once",
        headers=_auth_headers(idempotency_key="idem-rate-1"),
        json={"symbol": "BTC/USDT", "mode": "paper", "provider": "mock"},
    )
    second = client.post(
        "/operator/trading-loop/run-once",
        headers=_auth_headers(idempotency_key="idem-rate-2"),
        json={"symbol": "BTC/USDT", "mode": "paper", "provider": "mock"},
    )

    assert first.status_code == 200
    _assert_error_payload(
        second,
        status_code=429,
        error_code="guarded_rate_limited",
    )
