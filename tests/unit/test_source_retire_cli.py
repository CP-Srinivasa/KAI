"""Operator-manual source RETIRE (terminal kill) — core logic tests.

The Typer command is thin glue over ``retire_source``; we test the behaviour
(FSM-legal terminal transition, DB write, audit event, no-op on unknown/already-
retired) against a real in-memory SourceRepository, not the CLI plumbing.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.cli.commands.source import retire_source
from app.core.enums import AuthMode, SourceStatus, SourceType
from app.learning.source_lifecycle_audit import read_lifecycle_events
from app.storage.db.session import Base
from app.storage.repositories.source_repo import SourceRepository
from app.storage.schemas.source import SourceCreate

_NOW = datetime(2026, 6, 29, 12, 0, tzinfo=UTC)


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _seed(repo: SourceRepository, provider: str, status: SourceStatus) -> None:
    await repo.create(
        SourceCreate(
            source_type=SourceType.RSS_FEED,
            provider=provider,
            original_url=f"https://{provider}.example/feed",
            status=status,
            auth_mode=AuthMode.NONE,
        )
    )


@pytest.mark.asyncio
async def test_retire_sets_terminal_status_and_audits(session_factory, tmp_path) -> None:
    audit = tmp_path / "audit.jsonl"
    async with session_factory.begin() as session:
        await _seed(SourceRepository(session), "deadnews", SourceStatus.ACTIVE)

    async with session_factory.begin() as session:
        res = await retire_source(
            SourceRepository(session), "deadnews", reason="falsified", now=_NOW, audit_path=audit
        )
    assert res["applied"] is True
    assert res["from"] == "active"
    assert res["to"] == "retired"

    async with session_factory() as session:
        rows = await SourceRepository(session).list(provider="deadnews")
    assert rows[0].status == SourceStatus.RETIRED

    events = read_lifecycle_events(audit)
    assert events[-1].to_status == "retired"
    assert "falsified" in events[-1].reason


@pytest.mark.asyncio
async def test_retire_unknown_source_is_noop(session_factory, tmp_path) -> None:
    async with session_factory.begin() as session:
        res = await retire_source(
            SourceRepository(session),
            "ghost",
            reason="x",
            now=_NOW,
            audit_path=tmp_path / "a.jsonl",
        )
    assert res["applied"] is False
    assert res["reason"] == "no_such_source"


@pytest.mark.asyncio
async def test_retire_already_retired_is_noop(session_factory, tmp_path) -> None:
    async with session_factory.begin() as session:
        await _seed(SourceRepository(session), "dead2", SourceStatus.RETIRED)
    async with session_factory.begin() as session:
        res = await retire_source(
            SourceRepository(session),
            "dead2",
            reason="x",
            now=_NOW,
            audit_path=tmp_path / "a.jsonl",
        )
    assert res["applied"] is False
    assert res["reason"] == "already_retired"
