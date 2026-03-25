from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.analysis.factory import create_provider
from app.analysis.keywords.engine import KeywordEngine
from app.api.middleware.request_governance import RequestGovernanceMiddleware
from app.api.routers import alerts, dashboard, health, operator, query, research, sources
from app.core.logging import configure_logging, get_logger
from app.core.settings import get_settings
from app.ingestion.schedulers.rss_scheduler import RSSScheduler
from app.security.auth import setup_auth
from app.security.secrets import validate_secrets
from app.storage.db.session import build_session_factory

_logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    validate_secrets(settings)  # warn/fail on missing secrets at startup
    app.state.session_factory = build_session_factory(settings.db)

    # Build analysis components for full-pipeline mode
    keyword_engine = KeywordEngine.from_monitor_dir(Path(settings.monitor_dir))
    provider = None
    if settings.pipeline_provider:
        provider = create_provider(settings.pipeline_provider, settings)
        if provider is None:
            _logger.warning(
                "pipeline_provider_unavailable",
                provider=settings.pipeline_provider,
                hint="API key missing? Falling back to rule-based analysis only.",
            )
        else:
            _logger.info(
                "pipeline_provider_ready",
                provider=settings.pipeline_provider,
                cls=type(provider).__name__,
            )

    app.state.rss_scheduler = RSSScheduler(
        app.state.session_factory,
        interval_minutes=settings.pipeline_interval_minutes,
        keyword_engine=keyword_engine,
        provider=provider,
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
    # Request governance: request-ID propagation, body-size limit, audit JSONL.
    # Must be added after CORS (middleware stack is LIFO — governance runs first).
    app.add_middleware(
        RequestGovernanceMiddleware,
        max_body_bytes=settings.max_request_body_bytes,
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
