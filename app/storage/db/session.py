from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.settings import DBSettings


class Base(DeclarativeBase):
    pass


def build_engine(settings: DBSettings) -> AsyncEngine:
    return create_async_engine(
        settings.url,
        pool_size=settings.pool_size,
        max_overflow=settings.max_overflow,
        echo=settings.echo,
    )


def build_session_factory(settings: DBSettings) -> async_sessionmaker[AsyncSession]:
    engine = build_engine(settings)
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    async with session_factory() as session:
        yield session
