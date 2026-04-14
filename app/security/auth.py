"""API authentication middleware.

Implements a simple Bearer token check via the APP_API_KEY environment variable.
When APP_API_KEY is set, every request must include:

    Authorization: Bearer <APP_API_KEY>

When APP_API_KEY is empty the behaviour depends on the environment:
- development / dev / test / testing: auth disabled, warning logged once.
- all other environments (staging, production, qa, preview, …): startup fails
  with ConfigurationError — fail-closed by design.

Usage (attached to FastAPI in app/api/main.py):
    from app.security.auth import setup_auth
    setup_auth(app, settings.api_key, settings.env)
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from app.core.errors import ConfigurationError

logger = logging.getLogger(__name__)

# Environments where an empty API key is acceptable (local dev / CI).
_DEV_TEST_ENVS: frozenset[str] = frozenset({"development", "dev", "test", "testing"})

_AUTH_DISABLED_WARNED = False


def setup_auth(app: FastAPI, api_key: str, env: str = "development") -> None:
    """Attach bearer-token middleware to the FastAPI app.

    Args:
        app:     FastAPI application instance.
        api_key: Value of APP_API_KEY.
        env:     Value of APP_ENV (default: ``"development"``).

    Raises:
        ConfigurationError: if ``api_key`` is empty outside dev/test environments.
    """
    global _AUTH_DISABLED_WARNED

    if not api_key:
        if env.lower() not in _DEV_TEST_ENVS:
            raise ConfigurationError(
                f"APP_API_KEY is required in env='{env}'. "
                "Authentication cannot be disabled outside development/test contexts. "
                "Set APP_API_KEY in your environment."
            )
        if not _AUTH_DISABLED_WARNED:
            logger.warning(
                "API authentication is DISABLED. "
                "Set APP_API_KEY in your environment to protect all endpoints."
            )
            _AUTH_DISABLED_WARNED = True
        return  # no middleware attached

    @app.middleware("http")
    async def _bearer_auth(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Public read-only endpoints:
        # - /health for infra checks
        # - /dashboard/* as local operator HTML + API views (D-124 dashboard)
        path = request.url.path.rstrip("/")
        if path in ("/health",) or path.startswith("/dashboard"):
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing Authorization header"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = auth_header[len("Bearer ") :]
        # Use constant-time comparison to prevent timing attacks
        if not secrets.compare_digest(token, api_key):
            return JSONResponse(
                status_code=403,
                content={"detail": "Invalid API key"},
            )

        return await call_next(request)

    logger.info("API authentication enabled — Bearer token required for all endpoints")
