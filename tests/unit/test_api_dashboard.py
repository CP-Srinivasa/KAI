from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.api.routers import dashboard as dashboard_router
from app.api.routers import operator as operator_router
from app.core.settings import get_settings
from app.security.auth import setup_auth


def _make_dashboard_app(*, api_key: str, attach_auth_middleware: bool) -> FastAPI:
    app = FastAPI()
    app.include_router(dashboard_router.router)
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(api_key=api_key)
    if attach_auth_middleware:
        setup_auth(app, api_key)
    return app


def _daily_payload() -> dict[str, object]:
    return {
        "report_type": "daily_operator_summary",
        "readiness_status": "warning",
        "cycle_count_today": 2,
        "last_cycle_status": "no_signal",
        "last_cycle_symbol": "BTC/USDT",
        "last_cycle_at": "2026-03-22T12:00:00+00:00",
        "position_count": 1,
        "total_exposure_pct": 12.5,
        "mark_to_market_status": "ok",
        "decision_pack_status": "warning",
        "open_incidents": 1,
        "aggregated_at": "2026-03-22T12:05:00+00:00",
        "execution_enabled": False,
        "write_back_allowed": False,
    }


def test_dashboard_disabled_when_api_key_missing() -> None:
    app = _make_dashboard_app(api_key="", attach_auth_middleware=False)
    with TestClient(app) as client:
        response = client.get("/dashboard")

    assert response.status_code == 503
    body = response.json()
    assert body["detail"]["error"]["code"] == "dashboard_disabled"
    assert body["detail"]["execution_enabled"] is False
    assert body["detail"]["write_back_allowed"] is False


def test_dashboard_returns_html_response() -> None:
    app = _make_dashboard_app(api_key="test-key", attach_auth_middleware=True)
    with patch.object(
        dashboard_router.mcp_server,
        "get_daily_operator_summary",
        new_callable=AsyncMock,
        return_value=_daily_payload(),
    ):
        with TestClient(app) as client:
            response = client.get("/dashboard")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert '<meta http-equiv="refresh" content="60">' in response.text


def test_dashboard_shows_readiness_status() -> None:
    app = _make_dashboard_app(api_key="test-key", attach_auth_middleware=True)
    with patch.object(
        dashboard_router.mcp_server,
        "get_daily_operator_summary",
        new_callable=AsyncMock,
        return_value=_daily_payload(),
    ):
        with TestClient(app) as client:
            response = client.get("/dashboard")

    assert response.status_code == 200
    assert "readiness" in response.text.lower()
    assert "warning" in response.text.lower()


def test_dashboard_shows_execution_disabled() -> None:
    app = _make_dashboard_app(api_key="test-key", attach_auth_middleware=True)
    payload = _daily_payload()
    payload["execution_enabled"] = False
    payload["write_back_allowed"] = False
    with patch.object(
        dashboard_router.mcp_server,
        "get_daily_operator_summary",
        new_callable=AsyncMock,
        return_value=payload,
    ):
        with TestClient(app) as client:
            response = client.get("/dashboard")

    assert response.status_code == 200
    assert "execution_enabled=False" in response.text
    assert "write_back_allowed=False" in response.text


def test_dashboard_degrades_on_summary_error() -> None:
    app = _make_dashboard_app(api_key="test-key", attach_auth_middleware=True)
    with patch.object(
        dashboard_router.mcp_server,
        "get_daily_operator_summary",
        new_callable=AsyncMock,
        side_effect=RuntimeError("down"),
    ):
        with TestClient(app) as client:
            response = client.get("/dashboard")

    assert response.status_code == 200
    assert "dashboard unavailable" in response.text.lower()
    assert "status=unavailable" in response.text.lower()
    assert "traceback" not in response.text.lower()


def test_dashboard_truth_matches_operator_daily_summary_payload() -> None:
    payload = _daily_payload()
    payload["readiness_status"] = "error"
    payload["cycle_count_today"] = 9
    payload["last_cycle_status"] = "executed"
    payload["last_cycle_symbol"] = "ETH/USDT"
    payload["last_cycle_at"] = "2026-03-22T15:15:00+00:00"
    payload["position_count"] = 3
    payload["total_exposure_pct"] = 27.75
    payload["mark_to_market_status"] = "stale"
    payload["decision_pack_status"] = "blocked"
    payload["open_incidents"] = 4
    payload["aggregated_at"] = "2026-03-22T15:16:00+00:00"

    app = FastAPI()
    app.include_router(dashboard_router.router)
    app.include_router(operator_router.router)
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(api_key="test-key")

    with patch.object(
        dashboard_router.mcp_server,
        "get_daily_operator_summary",
        new_callable=AsyncMock,
        return_value=payload,
    ):
        with TestClient(app) as client:
            operator_response = client.get(
                "/operator/daily-summary",
                headers={"Authorization": "Bearer test-key"},
            )
            dashboard_response = client.get("/dashboard")

    assert operator_response.status_code == 200
    assert operator_response.json() == payload
    assert dashboard_response.status_code == 200

    html = dashboard_response.text
    assert "canonical source: get_daily_operator_summary" in html
    assert str(payload["readiness_status"]) in html
    assert str(payload["cycle_count_today"]) in html
    assert str(payload["last_cycle_status"]) in html
    assert str(payload["last_cycle_symbol"]) in html
    assert str(payload["last_cycle_at"]) in html
    assert f"{payload['position_count']} positions" in html
    assert f"{payload['total_exposure_pct']}%" in html
    assert str(payload["mark_to_market_status"]) in html
    assert str(payload["decision_pack_status"]) in html
    assert str(payload["open_incidents"]) in html
    assert f"aggregated_at={payload['aggregated_at']}" in html
    assert "execution_enabled=False" in html
    assert "write_back_allowed=False" in html


def test_dashboard_contains_static_drilldown_reference_section() -> None:
    app = _make_dashboard_app(api_key="test-key", attach_auth_middleware=True)
    with patch.object(
        dashboard_router.mcp_server,
        "get_daily_operator_summary",
        new_callable=AsyncMock,
        return_value=_daily_payload(),
    ):
        with TestClient(app) as client:
            response = client.get("/dashboard")

    assert response.status_code == 200
    html = response.text
    assert "Drilldown (Bearer required)" in html
    assert "/operator/readiness" in html
    assert "/operator/decision-pack" in html
    assert "/operator/trading-loop/recent-cycles" in html
    assert "/operator/review-journal" in html
    assert "/operator/resolution-summary" in html
    assert "<script" not in html.lower()


def test_dashboard_route_inventory_is_canonical_in_main_app() -> None:
    app = create_app()
    paths = {route.path for route in app.routes}

    assert "/dashboard" in paths
    assert "/static/dashboard.html" not in paths
