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
