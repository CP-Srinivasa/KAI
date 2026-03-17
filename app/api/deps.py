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


def get_document_repo(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> DocumentRepository:
    return DocumentRepository(session)
