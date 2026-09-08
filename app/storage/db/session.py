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


# Geteilte Fabriken je Engine-Identitaet. BEWUSST opt-in: ``build_session_factory``
# bleibt unveraendert, damit Aufrufer, die eine eigene, isolierte Engine brauchen
# (Tests mit sqlite ``:memory:``, CLI-Einmalprozesse), genau die weiter bekommen.
#
# Hintergrund (2026-09-08): ``build_trading_loop`` rief ``build_session_factory``
# aus dem Position-Monitor-Tick — im 60-Sekunden-Takt, also 1440-mal am Tag, jedes
# Mal eine neue AsyncEngine mit eigenem Pool, nie ``dispose()``-t. Der Event-Loop-
# max-Lag stieg im selben Prozess von 6,3 s (uptime ~600 s) auf 12,4 s (uptime
# 1152 s) — konsistent mit wachsendem lebendem Heap und damit teureren gen2-
# Sammlungen, die die GIL ununterbrechbar halten.
_SHARED_FACTORIES: dict[tuple[str, int, int, bool], async_sessionmaker[AsyncSession]] = {}


def _engine_identity(settings: DBSettings) -> tuple[str, int, int, bool]:
    """Alles, was ``build_engine`` unterscheidbar macht — nicht nur die URL.

    Zwei Aufrufer mit gleicher URL, aber abweichender Pool-Groesse muessen
    getrennte Engines bekommen, sonst erbt einer stillschweigend die Grenzen des
    anderen.
    """
    return (settings.url, settings.pool_size, settings.max_overflow, settings.echo)


def get_shared_session_factory(settings: DBSettings) -> async_sessionmaker[AsyncSession]:
    """Prozessweit geteilte Session-Fabrik fuer WIEDERKEHRENDE Aufrufer.

    Fuer alles, was auf einem Timer oder pro Request laeuft. Einmalige Aufrufer
    und alles, was Isolation braucht, nehmen weiter ``build_session_factory``.
    """
    key = _engine_identity(settings)
    factory = _SHARED_FACTORIES.get(key)
    if factory is None:
        factory = build_session_factory(settings)
        _SHARED_FACTORIES[key] = factory
    return factory


def reset_shared_session_factories_for_tests() -> None:
    """Cache leeren, damit Tests sich nicht gegenseitig Engines vererben."""
    _SHARED_FACTORIES.clear()


async def get_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    async with session_factory() as session:
        yield session
