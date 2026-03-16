"""FastAPI Application Entry Point."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.logging import configure_logging, get_logger
from app.core.settings import get_settings

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    configure_logging(log_level=settings.app.log_level.value, json_output=settings.app.is_production)
    logger.info("app_startup", name=settings.app.name, env=settings.app.env.value, version="0.1.0")
    yield
    logger.info("app_shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app.name,
        description="Modular AI Analyst & Trading Bot — multi-market monitoring, analysis, alerting.",
        version="0.1.0",
        docs_url="/docs" if not settings.app.is_production else None,
        redoc_url="/redoc" if not settings.app.is_production else None,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if not settings.app.is_production else [],
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["*"],
    )
    from app.api.routers import alerts, analysis, documents, health, query, sources, watchlists
    from app.api.routers import research, signals
    app.include_router(health.router, tags=["Health"])
    app.include_router(sources.router, prefix="/sources", tags=["Sources"])
    app.include_router(documents.router, prefix="/documents", tags=["Documents"])
    app.include_router(analysis.router, prefix="/analysis", tags=["Analysis"])
    app.include_router(query.router, prefix="/query", tags=["Query"])
    app.include_router(alerts.router, prefix="/alerts", tags=["Alerts"])
    app.include_router(watchlists.router, prefix="/watchlists", tags=["Watchlists"])
    app.include_router(research.router, prefix="/research", tags=["Research"])
    app.include_router(signals.router, prefix="/signals", tags=["Signals"])
    return app


app = create_app()
