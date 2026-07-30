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
async def test_compute_maturity_empty_store(session_factory, tmp_path) -> None:
    # artifacts_dir explizit auf ein leeres Verzeichnis — die Datei-Kinds
    # (tech_precision/exec_translation) duerfen nicht die Repo-Artefakte lesen.
    async with session_factory() as session:
        rows = await compute_maturity(session, artifacts_dir=tmp_path / "empty")
    assert all(r["due"] is False and r["n_proxy"] == 0 for r in rows)


@pytest.mark.asyncio
async def test_stories_level_counts_deduped_not_events(session_factory) -> None:
    """Gate-Level-Zaehlung (Lehre 2026-07-30): b20ef148 gatet auf stories —
    drei syndizierte Artikel derselben Story duerfen nur EINMAL reifen."""
    specs = (
        {
            "name": "drift_stories",
            "kind": "documents",
            "since_utc": "2026-07-02",
            "sources": None,
            "exclude_first_ticker": "BTC/USDT",
            "n_target": 2,
            "level": "stories",
        },
    )
    async with session_factory.begin() as session:
        session.add_all(
            [
                # Eine Story: gleiches Symbol/Seite, drei Quellen binnen 24h.
                _doc(1, source="coindesk", tickers=["ETH/USDT"], when="2026-07-03T00:00:00"),
                _doc(2, source="theblock", tickers=["ETH/USDT"], when="2026-07-03T06:00:00"),
                _doc(3, source="newsbtc", tickers=["ETH/USDT"], when="2026-07-03T12:00:00"),
                # Zweite Story: anderes Symbol.
                _doc(4, source="coindesk", tickers=["SOL/USDT"], when="2026-07-04T00:00:00"),
            ]
        )
    async with session_factory() as session:
        rows = await compute_maturity(session, specs=specs)
    drift = rows[0]
    assert drift["per_source"] == {"stories": 2, "events": 4}
    assert drift["n_proxy"] == 2  # Story-Level, NICHT 4 Events
    assert drift["due"] is True


@pytest.mark.asyncio
async def test_file_kind_specs_count_via_evaluators(session_factory, tmp_path) -> None:
    import json as _json

    art = tmp_path / "artifacts"
    art.mkdir()
    reg = "2026-07-29T09:14:47+00:00"

    def _w(name: str, rows: list[dict]) -> None:
        (art / name).write_text("".join(_json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    _w(
        "paper_execution_audit.jsonl",
        [
            {
                "event_type": "order_filled",
                "document_id": f"technical_paper_{d}",
                "filled_at": "2026-07-29T12:00:00+00:00",
                "timestamp_utc": "2026-07-29T12:00:00+00:00",
            }
            for d in ("A", "B", "C", "D")
        ]
        + [
            {
                "event_type": "position_closed",
                "document_id": "technical_paper_A",
                "timestamp_utc": "2026-07-29T13:00:00+00:00",
                "trade_pnl_usd": 4.0,
            }
        ],
    )
    _w(
        "alert_outcomes.jsonl",
        [
            {"document_id": "technical_paper_A", "outcome": "hit", "annotated_at": reg},
            {"document_id": "technical_paper_B", "outcome": "miss", "annotated_at": reg},
            {"document_id": "technical_paper_C", "outcome": "inconclusive", "annotated_at": reg},
        ],
    )
    specs = (
        {
            "name": "tech_like",
            "kind": "tech_precision",
            "since_utc": reg,
            "n_target": 2,
        },
        {
            "name": "exec_like",
            "kind": "exec_translation",
            "since_utc": reg,
            "n_target": 1,
        },
    )
    async with session_factory() as session:
        rows = await compute_maturity(session, specs=specs, artifacts_dir=art)

    tech = next(r for r in rows if r["name"] == "tech_like")
    assert tech["n_proxy"] == 2  # A hit + B miss; C inconclusive; D pending
    assert tech["per_source"] == {"resolved": 2, "inconclusive": 1, "pending": 1}
    assert tech["due"] is True

    ex = next(r for r in rows if r["name"] == "exec_like")
    assert ex["n_proxy"] == 1 and ex["due"] is True
