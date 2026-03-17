from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import health, query, sources
from app.core.logging import configure_logging
from app.core.settings import get_settings
from app.security.auth import setup_auth
from app.security.secrets import validate_secrets
from app.storage.db.session import build_session_factory


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    validate_secrets(settings)          # warn/fail on missing secrets at startup
    setup_auth(app, settings.api_key)   # attach bearer-token middleware if key is set
    app.state.session_factory = build_session_factory(settings.db)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    is_production = settings.env.lower() == "production"

    app = FastAPI(
        title="AI Analyst Trading Bot",
        description="Modular AI-powered market intelligence platform",
        version="0.1.0",
        lifespan=lifespan,
        # Disable interactive API docs in production — reduces attack surface
        docs_url=None if is_production else "/docs",
        redoc_url=None if is_production else "/redoc",
        openapi_url=None if is_production else "/openapi.json",
    )

    # CORS — restrict origins; extend via environment config if needed
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://localhost:8000"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    app.include_router(health.router)
    app.include_router(sources.router)
    app.include_router(query.router)
    return app


app = create_app()
