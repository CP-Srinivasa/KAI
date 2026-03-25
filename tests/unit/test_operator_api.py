"""Tests for FastAPI operator control plane router.

Verifies:
- Bearer token auth requirement (fail-closed)
- Read-only surface delegation
- Route inventory
- No trading/live semantics
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers.operator import router
from app.core.settings import get_settings


def _make_app(api_key: str = "") -> FastAPI:
    app = FastAPI()
    app.include_router(router)

    def _override_settings() -> SimpleNamespace:
        return SimpleNamespace(api_key=api_key)

    app.dependency_overrides[get_settings] = _override_settings
    return app


# ------------------------------------------------------------------
# Auth: fail-closed when APP_API_KEY not configured
# ------------------------------------------------------------------


def test_no_api_key_returns_503() -> None:
    c = TestClient(_make_app(api_key=""))
    r = c.get("/operator/status")
    assert r.status_code == 503


def test_missing_auth_header_returns_401() -> None:
    c = TestClient(_make_app(api_key="real-key"))
    r = c.get("/operator/status")
    assert r.status_code == 401


def test_wrong_token_returns_403() -> None:
    c = TestClient(_make_app(api_key="real-key"))
    r = c.get(
        "/operator/status",
        headers={"Authorization": "Bearer wrong-key"},
    )
    assert r.status_code == 403


def test_valid_token_passes_auth() -> None:
    c = TestClient(_make_app(api_key="test-key"))
    with patch.object(
        __import__(
            "app.agents.mcp_server",
            fromlist=["get_daily_operator_summary"],
        ),
        "get_daily_operator_summary",
        new_callable=AsyncMock,
        return_value={
            "report_type": "daily_operator_summary",
            "execution_enabled": False,
        },
    ):
        r = c.get(
            "/operator/status",
            headers={"Authorization": "Bearer test-key"},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["execution_enabled"] is False


# ------------------------------------------------------------------
# Route inventory
# ------------------------------------------------------------------


def test_expected_routes_present() -> None:
    paths = {route.path for route in router.routes}
    expected = {
        "/operator/status",
        "/operator/readiness",
        "/operator/decision-pack",
        "/operator/daily-summary",
        "/operator/portfolio-snapshot",
        "/operator/exposure-summary",
        "/operator/trading-loop/status",
        "/operator/trading-loop/recent-cycles",
        "/operator/trading-loop/run-once",
    }
    assert expected.issubset(paths), f"missing: {expected - paths}"


# ------------------------------------------------------------------
# No trading/live routes
# ------------------------------------------------------------------


def test_no_live_trading_routes() -> None:
    paths = [route.path for route in router.routes]
    forbidden = [
        "/operator/trade",
        "/operator/execute",
        "/operator/order",
        "/operator/fill",
        "/operator/broker",
        "/operator/live",
    ]
    for f in forbidden:
        assert f not in paths, f"has forbidden route: {f}"


def test_no_webhook_route() -> None:
    paths = [route.path for route in router.routes]
    assert "/operator/webhook" not in paths
