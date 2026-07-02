"""Shared FastAPI dependencies — session, repositories."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.storage.repositories.document_repo import DocumentRepository
from app.storage.repositories.source_repo import SourceRepository


async def get_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    async with request.app.state.session_factory() as session:
        async with session.begin():
            yield session


def get_source_repo(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SourceRepository:
    return SourceRepository(session)


async def get_source_repo_optional(
    request: Request,
) -> AsyncGenerator[SourceRepository | None, None]:
    """A SourceRepository when a session factory is wired, else ``None``.

    Read-only convenience for endpoints (e.g. the source-lifecycle truth-join) that
    want the DB when available but must still answer on a minimal app with no DB
    (tests, degraded boot). Never raises on a missing factory — yields ``None`` so
    the caller can skip the DB-dependent enrichment instead of returning a 500.
    """
    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:
        yield None
        return
    async with factory() as session:
        yield SourceRepository(session)


def get_document_repo(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> DocumentRepository:
    return DocumentRepository(session)
