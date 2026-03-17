"""API authentication middleware.

Implements a simple Bearer token check via the APP_API_KEY environment variable.
When APP_API_KEY is set, every request must include:

    Authorization: Bearer <APP_API_KEY>

When APP_API_KEY is empty (default in development), authentication is disabled
and a warning is logged once at startup.

Usage (attached to FastAPI in app/api/main.py):
    from app.security.auth import setup_auth
    setup_auth(app, settings)
"""

from __future__ import annotations

import logging
import secrets

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

_AUTH_DISABLED_WARNED = False


def setup_auth(app: FastAPI, api_key: str) -> None:
    """Attach bearer-token middleware to the FastAPI app.

    Args:
        app:     FastAPI application instance.
        api_key: Value of APP_API_KEY. If empty, auth is disabled (dev mode).
    """
    global _AUTH_DISABLED_WARNED

    if not api_key:
        if not _AUTH_DISABLED_WARNED:
            logger.warning(
                "API authentication is DISABLED. "
                "Set APP_API_KEY in your environment to protect all endpoints."
            )
            _AUTH_DISABLED_WARNED = True
        return  # no middleware attached

    @app.middleware("http")
    async def _bearer_auth(request: Request, call_next) -> Response:
        # Health endpoint is always public (needed for Docker healthchecks)
        if request.url.path in ("/health", "/health/"):
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing Authorization header"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = auth_header[len("Bearer "):]
        # Use constant-time comparison to prevent timing attacks
        if not secrets.compare_digest(token, api_key):
            return JSONResponse(
                status_code=403,
                content={"detail": "Invalid API key"},
            )

        return await call_next(request)

    logger.info("API authentication enabled — Bearer token required for all endpoints")
