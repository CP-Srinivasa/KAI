"""Tests für die Onboarding-Execution (Phase 3b) gegen eine In-Memory-DB."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.enums import AuthMode, SourceStatus, SourceType
from app.learning.source_graduation import GraduationSwap
from app.learning.source_intake_gate import CandidateAccess, IntakeDecision, SourceCandidate
from app.learning.source_onboarding import (
    build_probation_candidates,
    execute_swaps,
    onboard_accepted,
)
from app.storage.db.session import Base
from app.storage.repositories.source_repo import SourceRepository
from app.storage.schemas.source import SourceCreate


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


def _acc(url: str, provider: str) -> tuple[SourceCandidate, IntakeDecision]:
    cand = SourceCandidate(url, CandidateAccess.RSS, SourceType.RSS_FEED, provider=provider)
    dec = IntakeDecision(True, "accepted: rss", SourceStatus.PROBATION, False, url)
    return (cand, dec)


async def _seed(repo: SourceRepository, provider: str, url: str, status: SourceStatus) -> None:
    await repo.create(
        SourceCreate(
            source_type=SourceType.RSS_FEED,
            provider=provider,
            original_url=url,
            status=status,
            auth_mode=AuthMode.NONE,
        )
    )


@pytest.mark.asyncio
async def test_onboard_creates_probation_and_is_idempotent(session_factory) -> None:
    async with session_factory.begin() as session:
        repo = SourceRepository(session)
        await _seed(repo, "decrypt", "https://decrypt.co/feed", SourceStatus.ACTIVE)

    async with session_factory.begin() as session:
        repo = SourceRepository(session)
        results = await onboard_accepted(
            repo,
            [
                _acc("https://protos.com/feed/", "protos"),  # neu
                _acc("https://decrypt.co/feed", "decrypt2"),  # URL schon da
                _acc("https://other.com/feed", "decrypt"),  # provider schon da
            ],
        )
    by_reason = {r.url: r.reason for r in results}
    assert by_reason["https://protos.com/feed/"] == "onboarded_probation"
    assert by_reason["https://decrypt.co/feed"] == "duplicate_url"
    assert by_reason["https://other.com/feed"] == "duplicate_provider"

    async with session_factory() as session:
        repo = SourceRepository(session)
        prob = await repo.list(status=SourceStatus.PROBATION)
    assert [s.provider for s in prob] == ["protos"]

    # Zweiter Lauf desselben Kandidaten → kein zweites Anlegen (idempotent).
    async with session_factory.begin() as session:
        repo = SourceRepository(session)
        again = await onboard_accepted(repo, [_acc("https://protos.com/feed/", "protos")])
    assert again[0].reason == "duplicate_url"


@pytest.mark.asyncio
async def test_build_probation_candidates_uses_ranking_evidence(session_factory) -> None:
    async with session_factory.begin() as session:
        repo = SourceRepository(session)
        await _seed(repo, "protos", "https://protos.com/feed/", SourceStatus.PROBATION)
        await _seed(repo, "newsbtc", "https://newsbtc.com/feed/", SourceStatus.PROBATION)

    async with session_factory() as session:
        repo = SourceRepository(session)
        cands = await build_probation_candidates(
            repo,
            evidence_by_source={
                "protos": {"n": 12, "wilson_lower_95": 0.61},
                # newsbtc: keine Evidenz → score 0 / deliveries 0 (graduiert nicht)
            },
            runs_by_source={"protos": 4, "newsbtc": 1},
        )
    by_name = {c.source: c for c in cands}
    assert by_name["protos"].score == 0.61
    assert by_name["protos"].deliveries == 12
    assert by_name["protos"].runs == 4
    assert by_name["newsbtc"].score == 0.0
    assert by_name["newsbtc"].deliveries == 0


@pytest.mark.asyncio
async def test_build_probation_candidates_carries_delivering_flag(session_factory) -> None:
    """The sustained-delivery floor flows into ProbationCandidate.delivering."""
    async with session_factory.begin() as session:
        repo = SourceRepository(session)
        await _seed(repo, "ctel", "https://cointelegraph.com/rss", SourceStatus.PROBATION)
        await _seed(repo, "quiet", "https://quiet.com/feed/", SourceStatus.PROBATION)

    async with session_factory() as session:
        repo = SourceRepository(session)
        cands = await build_probation_candidates(
            repo,
            evidence_by_source={
                "ctel": {"n": 0, "wilson_lower_95": 0.0, "delivering": True},
                "quiet": {"n": 0, "wilson_lower_95": 0.0, "delivering": False},
            },
            runs_by_source={"ctel": 5, "quiet": 5},
        )
    by_name = {c.source: c for c in cands}
    assert by_name["ctel"].delivering is True  # zero directional, but delivering
    assert by_name["quiet"].delivering is False


@pytest.mark.asyncio
async def test_execute_swaps_promotes_and_archives(session_factory) -> None:
    async with session_factory.begin() as session:
        repo = SourceRepository(session)
        await _seed(repo, "newsrc", "https://new.com/feed", SourceStatus.PROBATION)
        await _seed(repo, "oldsrc", "https://old.com/feed", SourceStatus.ACTIVE)

    async with session_factory.begin() as session:
        repo = SourceRepository(session)
        results = await execute_swaps(
            repo,
            [
                GraduationSwap(
                    promote="newsrc", archive="oldsrc", promote_score=0.7, archive_score=0.2
                )
            ],
        )
    assert results[0].promoted is True
    assert results[0].archived is True

    async with session_factory() as session:
        repo = SourceRepository(session)
        status = {s.provider: s.status for s in await repo.list()}
    assert status["newsrc"] == SourceStatus.ACTIVE
    assert status["oldsrc"] == SourceStatus.ARCHIVED


@pytest.mark.asyncio
async def test_execute_swaps_missing_source_blocks_only_that_swap(session_factory) -> None:
    async with session_factory.begin() as session:
        repo = SourceRepository(session)
        await _seed(repo, "newsrc", "https://new.com/feed", SourceStatus.PROBATION)

    async with session_factory.begin() as session:
        repo = SourceRepository(session)
        results = await execute_swaps(
            repo,
            [
                GraduationSwap(
                    promote="newsrc", archive="ghost", promote_score=0.7, archive_score=0.2
                )
            ],
        )
    # promote ok, archive-Partner fehlt → Swap halb, sauber gemeldet.
    assert results[0].promoted is True
    assert results[0].archived is False
    assert "source_not_found" in results[0].reason
