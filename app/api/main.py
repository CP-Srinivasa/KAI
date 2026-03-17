from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routers import health, query, sources
from app.core.logging import configure_logging
from app.core.settings import get_settings
from app.storage.db.session import build_session_factory


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    app.state.session_factory = build_session_factory(settings.db)
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI Analyst Trading Bot",
        description="Modular AI-powered market intelligence platform",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(health.router)
    app.include_router(sources.router)
    app.include_router(query.router)
    return app


app = create_app()
