"""Unit tests for out-of-sample maturity counting of open pre-registrations."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.research.prereg_maturity import compute_maturity
from app.storage.db.session import Base
from app.storage.models.document import CanonicalDocumentModel


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


def _doc(i: int, *, source: str, tickers: list[str], when: str) -> CanonicalDocumentModel:
    return CanonicalDocumentModel(
        id=f"d{i}",
        url=f"u{i}",
        title=f"t{i}",
        document_type="news",
        status="analyzed",
        market_scope="crypto",
        source_name=source,
        sentiment_label="bullish",
        tickers=tickers,
        published_at=datetime.fromisoformat(when).replace(tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_compute_maturity_counts_and_due_flags(session_factory) -> None:
    specs = (
        {
            "name": "drift_like",
            "since_utc": "2026-07-02",
            "sources": None,
            "exclude_first_ticker": "BTC/USDT",
            "n_target": 2,
        },
        {
            "name": "per_source_like",
            "since_utc": "2026-07-01",
            "sources": ("theblock", "newsbtc"),
            "exclude_first_ticker": None,
            "n_target": 1,
        },
    )
    async with session_factory.begin() as session:
        session.add_all(
            [
                # counted by drift_like: after since, non-BTC first ticker
                _doc(1, source="coindesk", tickers=["ETH/USDT"], when="2026-07-03T00:00:00"),
                _doc(2, source="theblock", tickers=["SOL/USDT"], when="2026-07-04T00:00:00"),
                # excluded from drift_like (BTC first ticker), counted for theblock
                _doc(3, source="theblock", tickers=["BTC/USDT"], when="2026-07-03T00:00:00"),
                # too old for drift_like's window
                _doc(4, source="coindesk", tickers=["ETH/USDT"], when="2026-06-01T00:00:00"),
            ]
        )
    async with session_factory() as session:
        rows = await compute_maturity(session, specs=specs)

    drift = next(r for r in rows if r["name"] == "drift_like")
    assert drift["n_proxy"] == 2
    assert drift["due"] is True

    per_src = next(r for r in rows if r["name"] == "per_source_like")
    assert per_src["per_source"] == {"theblock": 2, "newsbtc": 0}
    assert per_src["due"] is False  # newsbtc below per-source target


@pytest.mark.asyncio
async def test_compute_maturity_empty_store(session_factory) -> None:
    async with session_factory() as session:
        rows = await compute_maturity(session)
    assert all(r["due"] is False and r["n_proxy"] == 0 for r in rows)
