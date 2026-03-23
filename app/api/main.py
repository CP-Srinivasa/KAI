from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import alerts, dashboard, health, operator, query, research, sources
from app.core.logging import configure_logging
from app.core.settings import get_settings
from app.ingestion.base.interfaces import FetchResult
from app.ingestion.schedulers.rss_scheduler import RSSScheduler
from app.security.auth import setup_auth
from app.security.secrets import validate_secrets
from app.storage.db.session import build_session_factory
from app.storage.document_ingest import persist_fetch_result


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    validate_secrets(settings)  # warn/fail on missing secrets at startup
    app.state.session_factory = build_session_factory(settings.db)

    async def persist_result(result: FetchResult) -> None:
        await persist_fetch_result(app.state.session_factory, result)

    app.state.rss_scheduler = RSSScheduler(
        app.state.session_factory,
        persist_result=persist_result,
    )
    app.state.rss_scheduler.start()
    try:
        yield
    finally:
        app.state.rss_scheduler.stop()


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

    # CORS — origins controlled via APP_CORS_ALLOWED_ORIGINS (comma-separated env var)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Request-ID",
            "X-Correlation-ID",
            "Idempotency-Key",
        ],
    )
    setup_auth(app, settings.api_key, settings.env)  # attach bearer-token middleware before startup

    app.include_router(health.router)
    app.include_router(sources.router)
    app.include_router(query.router)
    app.include_router(alerts.router)
    app.include_router(research.router, prefix="/research", tags=["research"])
    app.include_router(operator.router)
    app.include_router(dashboard.router)

    return app


app = create_app()
