"""Frist auf n-basierten Prä-Regs: ein Claim muss enden können.

H1/H2 trugen nur ein ``n_min`` und keine Frist. H2 stand deshalb dauerhaft bei
14/50 — weder PASS noch FAIL, unbegrenzt „reifend", weil strukturell nur ~26 %
der geschlossenen Trades die Population je erreichen konnten. Ein Claim ohne
Ende bindet Aufmerksamkeit und liefert nie eine Erkenntnis.

Diese Tests halten das Verhalten der Frist fest:
* Fenster abgelaufen UND n<n_target ⇒ fällig als Timeout (kein Sachverdikt).
* Fenster abgelaufen, aber n_target erreicht ⇒ normal JUDGEABLE — die Frist
  ist eine Bremse gegen Zombies, kein Deckel auf erreichte Evidenz.
* Ohne ``window_end_utc`` bleibt alles wie bisher (rein additiv).
"""

from __future__ import annotations

import json as _json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.research.prereg_maturity import (
    STATE_EVAL_CHECK_DUE,
    STATE_JUDGEABLE,
    STATE_NOT_DUE,
    compute_maturity,
)
from app.storage.db.session import Base

REG = "2026-08-08T10:41:26+00:00"
CLOSE_TS = "2026-08-09T12:00:00+00:00"
WINDOW_END = "2026-09-22T00:00:00+00:00"


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


def _artifacts(tmp_path: Path, n_hits: int) -> Path:
    art = tmp_path / "artifacts"
    art.mkdir(exist_ok=True)

    def _w(name: str, rows: list[dict]) -> None:
        (art / name).write_text("".join(_json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    _w(
        "alert_outcomes.jsonl",
        [{"document_id": f"d{i}", "outcome": "hit", "annotated_at": REG} for i in range(n_hits)],
    )
    _w(
        "paper_execution_audit.jsonl",
        [
            {
                "event_type": "position_closed",
                "document_id": f"d{i}",
                "timestamp_utc": CLOSE_TS,
                "signal_source": "technical_paper",
                "trade_pnl_usd": 5.0,
            }
            for i in range(n_hits)
        ],
    )
    return art


def _spec(n_target: int, *, window_end: str | None = WINDOW_END) -> dict:
    spec = {
        "name": "hit_to_win_like",
        "prereg_id": "26d3e0eb29f553f3",
        "kind": "hit_to_win",
        "since_utc": REG,
        "n_target": n_target,
        "gate_horizon_s": 86400,
    }
    if window_end is not None:
        spec["window_end_utc"] = window_end
    return spec


async def _rows(session_factory, art: Path, spec: dict, now: datetime) -> dict:
    async with session_factory() as session:
        rows = await compute_maturity(session, specs=(spec,), artifacts_dir=art, now=now)
    return rows[0]


@pytest.mark.asyncio
async def test_expired_window_below_target_becomes_due_as_timeout(
    session_factory, tmp_path
) -> None:
    """Der H2-Fall: Frist verstrichen, n_min nie erreicht ⇒ Ende statt Limbo."""
    art = _artifacts(tmp_path, n_hits=5)
    after = datetime(2026, 9, 23, tzinfo=UTC)
    row = await _rows(session_factory, art, _spec(30), after)

    assert row["n_proxy"] == 5
    assert row["timed_out"] is True
    assert row["state"] == STATE_EVAL_CHECK_DUE
    assert row["state_source"] == "window_timeout"
    assert row["due"] is True
    assert row["window_end_utc"] == WINDOW_END


@pytest.mark.asyncio
async def test_expired_window_with_target_reached_stays_judgeable(
    session_factory, tmp_path
) -> None:
    """Die Frist bremst kein erreichtes n — sonst würde sie Evidenz vernichten."""
    art = _artifacts(tmp_path, n_hits=30)
    after = datetime(2026, 9, 23, tzinfo=UTC)
    row = await _rows(session_factory, art, _spec(30), after)

    assert row["n_proxy"] == 30
    assert row["timed_out"] is False
    assert row["state"] == STATE_JUDGEABLE
    assert row["due"] is True


@pytest.mark.asyncio
async def test_window_open_below_target_is_not_due(session_factory, tmp_path) -> None:
    """Vor Fristablauf verhält sich alles unverändert."""
    art = _artifacts(tmp_path, n_hits=5)
    before = datetime(2026, 8, 20, tzinfo=UTC)
    row = await _rows(session_factory, art, _spec(30), before)

    assert row["timed_out"] is False
    assert row["state"] == STATE_NOT_DUE
    assert row["due"] is False


@pytest.mark.asyncio
async def test_spec_without_window_is_unchanged(session_factory, tmp_path) -> None:
    """Rein additiv: fristlose Specs (H1) verhalten sich exakt wie bisher."""
    art = _artifacts(tmp_path, n_hits=5)
    far_future = datetime(2027, 1, 1, tzinfo=UTC)
    row = await _rows(session_factory, art, _spec(30, window_end=None), far_future)

    assert row["timed_out"] is False
    assert row["window_end_utc"] is None
    assert row["state"] == STATE_NOT_DUE


@pytest.mark.asyncio
async def test_hit_to_win_detail_separates_gating_from_diagnostic(
    session_factory, tmp_path
) -> None:
    """Die Reifezählung nimmt NUR die hit-Kohorte — miss ist Beiwerk."""
    art = tmp_path / "artifacts"
    art.mkdir()

    def _w(name: str, rows: list[dict]) -> None:
        (art / name).write_text("".join(_json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    _w(
        "alert_outcomes.jsonl",
        [{"document_id": "h1", "outcome": "hit", "annotated_at": REG}]
        + [{"document_id": f"m{i}", "outcome": "miss", "annotated_at": REG} for i in range(9)],
    )
    _w(
        "paper_execution_audit.jsonl",
        [
            {
                "event_type": "position_closed",
                "document_id": doc,
                "timestamp_utc": CLOSE_TS,
                "signal_source": "technical_paper",
                "trade_pnl_usd": 5.0,
            }
            for doc in ["h1"] + [f"m{i}" for i in range(9)]
        ]
        + [
            {
                "event_type": "position_closed",
                "document_id": "uuid-x",
                "timestamp_utc": CLOSE_TS,
                "signal_source": "real_analysis",
                "trade_pnl_usd": 1.0,
            }
        ],
    )
    row = await _rows(session_factory, art, _spec(30), datetime(2026, 8, 20, tzinfo=UTC))

    assert row["n_proxy"] == 1, "nur die hit-Kohorte ist reiferelevant"
    assert row["per_source"]["n_hit_gating"] == 1
    assert row["per_source"]["n_miss_diagnostic"] == 9
    assert row["per_source"]["absent_from_ledger"] == 1
