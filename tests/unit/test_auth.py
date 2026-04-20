"""Tests for app.security.auth — bearer-token middleware and env-gating."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.errors import ConfigurationError
from app.security.auth import (
    _DEV_TEST_ENVS,
    setup_auth,
)

# ---------------------------------------------------------------------------
# Fail-closed: non-dev/test environments must reject empty API key
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("env", ["production", "staging", "qa", "preview", "PRODUCTION"])
def test_setup_auth_raises_for_empty_key_outside_dev(env: str) -> None:
    app = FastAPI()
    with pytest.raises(ConfigurationError, match="APP_API_KEY is required"):
        setup_auth(app, api_key="", env=env)


# ---------------------------------------------------------------------------
# Dev/test environments: empty key is accepted (warning only)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("env", ["development", "dev", "test", "testing"])
def test_setup_auth_allows_empty_key_in_dev_test(env: str) -> None:
    app = FastAPI()
    setup_auth(app, api_key="", env=env)  # must not raise


def test_dev_test_envs_constant_matches_parametrize() -> None:
    assert "development" in _DEV_TEST_ENVS
    assert "dev" in _DEV_TEST_ENVS
    assert "test" in _DEV_TEST_ENVS
    assert "testing" in _DEV_TEST_ENVS
    assert "production" not in _DEV_TEST_ENVS
    assert "staging" not in _DEV_TEST_ENVS


# ---------------------------------------------------------------------------
# Default env="development" maintains backward-compatibility
# ---------------------------------------------------------------------------


def test_setup_auth_default_env_accepts_empty_key() -> None:
    """Calling setup_auth(app, "") without env must not raise (dev default)."""
    app = FastAPI()
    setup_auth(app, api_key="")  # env defaults to "development"


# ---------------------------------------------------------------------------
# Middleware: valid key attaches bearer protection
# ---------------------------------------------------------------------------


def _app_with_auth(api_key: str, env: str = "production") -> FastAPI:
    app = FastAPI()

    @app.get("/protected")
    async def _protected() -> dict[str, str]:
        return {"ok": "true"}

    @app.get("/health")
    async def _health() -> dict[str, str]:
        return {"status": "ok"}

    setup_auth(app, api_key=api_key, env=env)
    return app


def test_bearer_auth_accepts_valid_token() -> None:
    app = _app_with_auth("secret-key")
    with TestClient(app) as client:
        response = client.get("/protected", headers={"Authorization": "Bearer secret-key"})
    assert response.status_code == 200


def test_bearer_auth_rejects_missing_header() -> None:
    app = _app_with_auth("secret-key")
    with TestClient(app) as client:
        response = client.get("/protected")
    assert response.status_code == 401
    assert response.json()["detail"] == "Missing Authorization header"


def test_bearer_auth_rejects_wrong_token() -> None:
    app = _app_with_auth("secret-key")
    with TestClient(app) as client:
        response = client.get("/protected", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 403
    assert response.json()["detail"] == "Invalid API key"


def test_bearer_auth_skips_health_endpoint() -> None:
    app = _app_with_auth("secret-key")
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# No middleware attached in dev mode: all requests pass through
# ---------------------------------------------------------------------------


def test_no_middleware_in_dev_mode_allows_unauthenticated() -> None:
    app = FastAPI()

    @app.get("/open")
    async def _open() -> dict[str, str]:
        return {"ok": "true"}

    setup_auth(app, api_key="", env="development")
    with TestClient(app) as client:
        response = client.get("/open")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# D-156d: Dashboard Defense-in-Depth — tunnel traffic must carry CF-Access
# ---------------------------------------------------------------------------


def _app_with_dashboard(api_key: str, cf_allowed: list[str] | None = None) -> FastAPI:
    app = FastAPI()

    @app.get("/dashboard/api/ping")
    async def _dash() -> dict[str, str]:
        return {"ok": "true"}

    setup_auth(
        app,
        api_key=api_key,
        env="production",
        cf_allowed_emails=cf_allowed or [],
    )
    return app


def test_dashboard_local_traffic_no_cfray_still_open() -> None:
    app = _app_with_dashboard("secret", cf_allowed=["ops@example.com"])
    with TestClient(app) as client:
        response = client.get("/dashboard/api/ping")
    assert response.status_code == 200


def test_dashboard_tunnel_traffic_without_cf_email_rejected() -> None:
    app = _app_with_dashboard("secret", cf_allowed=["ops@example.com"])
    with TestClient(app) as client:
        response = client.get(
            "/dashboard/api/ping",
            headers={"Cf-Ray": "abc-FRA", "Cf-Connecting-IP": "203.0.113.7"},
        )
    assert response.status_code == 401
    assert "Cloudflare Access" in response.json()["detail"]


def test_dashboard_tunnel_traffic_with_wrong_cf_email_rejected() -> None:
    app = _app_with_dashboard("secret", cf_allowed=["ops@example.com"])
    with TestClient(app) as client:
        response = client.get(
            "/dashboard/api/ping",
            headers={
                "Cf-Ray": "abc-FRA",
                "Cf-Connecting-IP": "203.0.113.7",
                "Cf-Access-Authenticated-User-Email": "attacker@evil.test",
            },
        )
    assert response.status_code == 401


def test_dashboard_tunnel_traffic_with_allowed_cf_email_accepted() -> None:
    app = _app_with_dashboard("secret", cf_allowed=["ops@example.com"])
    with TestClient(app) as client:
        response = client.get(
            "/dashboard/api/ping",
            headers={
                "Cf-Ray": "abc-FRA",
                "Cf-Connecting-IP": "203.0.113.7",
                "Cf-Access-Authenticated-User-Email": "ops@example.com",
            },
        )
    assert response.status_code == 200


def test_dashboard_no_cf_allowlist_configured_stays_open() -> None:
    """If operator runs without CF emails (pure Bearer setup), /dashboard stays open."""
    app = _app_with_dashboard("secret", cf_allowed=[])
    with TestClient(app) as client:
        response = client.get("/dashboard/api/ping", headers={"Cf-Ray": "abc-FRA"})
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# NEO-P-001 (A): Cf-Ray without Cf-Connecting-IP is rejected as orphan
# ---------------------------------------------------------------------------


def test_dashboard_cf_ray_without_connecting_ip_rejected() -> None:
    """A non-CF reverse proxy that forwards Cf-Ray alone must be rejected.

    Cloudflare sets Cf-Ray AND Cf-Connecting-IP on every edge-authenticated
    request. Missing Cf-Connecting-IP = not from the real CF edge.
    """
    app = _app_with_dashboard("secret", cf_allowed=["ops@example.com"])
    with TestClient(app) as client:
        response = client.get(
            "/dashboard/api/ping",
            headers={
                "Cf-Ray": "abc-FRA",
                "Cf-Access-Authenticated-User-Email": "ops@example.com",
            },
        )
    assert response.status_code == 401
    assert "Cloudflare Access" in response.json()["detail"]


def test_dashboard_cf_ray_orphan_rejected_even_with_allowed_email() -> None:
    """Even an allowlisted email does not rescue an orphan Cf-Ray request."""
    app = _app_with_dashboard("secret", cf_allowed=["ops@example.com"])
    with TestClient(app) as client:
        response = client.get(
            "/dashboard/api/ping",
            headers={
                "Cf-Ray": "abc-FRA",
                # Cf-Connecting-IP deliberately missing
                "Cf-Access-Authenticated-User-Email": "ops@example.com",
            },
        )
    assert response.status_code == 401


def test_dashboard_like_path_falls_under_bearer_not_defense_in_depth() -> None:
    """NEO-F-004: /dashboardv2-style path is NOT treated as /dashboard/*."""
    app = FastAPI()

    @app.get("/dashboardv2/report")
    async def _sibling() -> dict[str, str]:
        return {"ok": "true"}

    setup_auth(app, api_key="secret", env="production", cf_allowed_emails=["ops@example.com"])
    with TestClient(app) as client:
        # Tunnel traffic without Bearer and without CF-Access-Email
        # must be rejected by the *Bearer* branch (401 missing Auth),
        # NOT silently allowed by the defense-in-depth branch.
        response = client.get("/dashboardv2/report", headers={"Cf-Ray": "abc-FRA"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Missing Authorization header"


# ---------------------------------------------------------------------------
# SENTR-F-002: Defense-in-Depth — /tradingview/webhook middleware gate
# ---------------------------------------------------------------------------


def test_tradingview_webhook_middleware_rejects_when_disabled() -> None:
    """With tv_webhook_enabled=False the middleware returns 404 before the router."""
    app = FastAPI()

    @app.post("/tradingview/webhook")
    async def _leak() -> dict[str, str]:
        # Would leak payload if reached — middleware must block.
        return {"leaked": "true"}

    setup_auth(app, api_key="secret", env="production", tv_webhook_enabled=False)
    with TestClient(app) as client:
        response = client.post("/tradingview/webhook", json={"x": 1})
    assert response.status_code == 404
    assert response.json()["detail"] == "Not Found"


def test_tradingview_webhook_middleware_allows_when_enabled() -> None:
    """With tv_webhook_enabled=True the middleware passes through to the router."""
    app = FastAPI()

    @app.post("/tradingview/webhook")
    async def _echo() -> dict[str, str]:
        return {"ok": "true"}

    setup_auth(app, api_key="secret", env="production", tv_webhook_enabled=True)
    with TestClient(app) as client:
        response = client.post("/tradingview/webhook", json={"x": 1})
    assert response.status_code == 200
    assert response.json() == {"ok": "true"}
