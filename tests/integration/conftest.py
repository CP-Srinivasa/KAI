"""Shared fixtures for integration tests.

All integration tests use a real SQLite in-memory database.
StaticPool ensures every async session shares the same connection,
so data written in one session is visible in the next.
"""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# Import all ORM models so Base.metadata discovers their tables.
import app.storage.models.document  # noqa: F401
import app.storage.models.event  # noqa: F401
import app.storage.models.source  # noqa: F401
import app.storage.models.trading  # noqa: F401
from app.storage.db.session import Base


@pytest.fixture(scope="function")
async def sqlite_engine() -> AsyncEngine:
    """In-memory SQLite engine — fresh schema per test function."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture(scope="function")
def session_factory(sqlite_engine: AsyncEngine) -> async_sessionmaker:
    """Session factory bound to the in-memory SQLite engine."""
    return async_sessionmaker(sqlite_engine, expire_on_commit=False)
