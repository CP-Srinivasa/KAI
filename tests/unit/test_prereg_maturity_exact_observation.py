"""Der Proxy darf keine Fälligkeit melden, wenn eine EXAKTE Messung vorliegt.

Befund 2026-08-02 (zweiter Wiederholungsfall nach 07-30): ``prereg-maturity``
meldete ``EVAL_CHECK_DUE`` für ``b20ef1487ccba99d`` aus einem Story-Proxy von
380, während der exakte Evaluator am Gate-Horizont 86400 s **273/300** zählte.
Drei getrennte Ursachen, hier getestet:

1. Der Spec-Anker stand auf Mitternacht (``2026-07-02``) statt auf dem
   versiegelten ``created_at_utc`` (``2026-07-02T05:43:32.211092+00:00``) —
   gemessen 19 Stories aus der Zeit VOR der Registrierung (381 → 362).
2. Stories, deren Gate-Horizont noch nicht verstrichen ist, KÖNNEN nicht
   aufgelöst sein; der Proxy zählte sie mit (362 → 355).
3. Auch der bereinigte Proxy bleibt eine Obergrenze (355 vs. exakt 273). Solange
   eine frische exakte Beobachtung existiert, dominiert sie den Proxy.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.research.prereg_maturity import (
    EXACT_OBSERVATION_MAX_AGE_DAYS,
    MATURITY_SPECS,
    STATE_EVAL_CHECK_DUE,
    STATE_JUDGEABLE,
    STATE_NOT_DUE,
    compute_maturity,
    record_exact_observation,
)
from app.storage.db.session import Base
from app.storage.models.document import CanonicalDocumentModel

SEALED_ANCHOR = "2026-07-02T05:43:32.211092+00:00"
NOW = datetime(2026, 8, 2, 8, 25, tzinfo=UTC)


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


def _doc(i: int, *, symbol: str, when: datetime) -> CanonicalDocumentModel:
    return CanonicalDocumentModel(
        id=f"d{i}",
        url=f"u{i}",
        title=f"t{i}",
        document_type="news",
        status="analyzed",
        market_scope="crypto",
        source_name="coindesk",
        sentiment_label="bullish",
        tickers=[symbol],
        published_at=when,
    )


def _spec(**over) -> dict:
    base = {
        "name": "drift_like",
        "prereg_id": "b20ef1487ccba99d",
        "kind": "documents",
        "since_utc": SEALED_ANCHOR,
        "sources": None,
        "exclude_first_ticker": "BTC/USDT",
        "n_target": 2,
        "level": "stories",
        "gate_horizon_s": 86400,
    }
    base.update(over)
    return base


def _observe(tmp_path: Path, rows: list[dict]) -> Path:
    research = tmp_path / "research"
    research.mkdir(parents=True, exist_ok=True)
    path = research / "prereg_exact_observations.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


# ── 1. Der Anker ist der versiegelte Zeitstempel, nicht Mitternacht ──────────


def test_sealed_spec_anchors_on_the_ledger_timestamp() -> None:
    spec = next(s for s in MATURITY_SPECS if s["prereg_id"] == "b20ef1487ccba99d")
    assert spec["since_utc"] == SEALED_ANCHOR
    assert spec["gate_horizon_s"] == 86400


@pytest.mark.asyncio
async def test_stories_before_the_sealed_anchor_are_not_counted(
    session_factory, tmp_path: Path
) -> None:
    async with session_factory.begin() as session:
        session.add_all(
            [
                # 4 h vor der Registrierung publiziert → gehört NICHT in die OOS-Kohorte
                _doc(1, symbol="ETH/USDT", when=datetime(2026, 7, 2, 1, 0, tzinfo=UTC)),
                _doc(2, symbol="SOL/USDT", when=datetime(2026, 7, 3, 0, 0, tzinfo=UTC)),
            ]
        )
    async with session_factory() as session:
        rows = await compute_maturity(
            session, specs=(_spec(n_target=99),), artifacts_dir=tmp_path, now=NOW
        )

    assert rows[0]["n_proxy"] == 1


# ── 2. Unreife Stories (Gate-Horizont nicht verstrichen) zählen nicht ────────


@pytest.mark.asyncio
async def test_stories_inside_the_gate_horizon_are_not_counted(
    session_factory, tmp_path: Path
) -> None:
    async with session_factory.begin() as session:
        session.add_all(
            [
                _doc(1, symbol="ETH/USDT", when=NOW - timedelta(days=5)),
                # 3 h alt — der 24h-Horizont ist noch nicht verstrichen
                _doc(2, symbol="SOL/USDT", when=NOW - timedelta(hours=3)),
            ]
        )
    async with session_factory() as session:
        rows = await compute_maturity(
            session, specs=(_spec(n_target=99),), artifacts_dir=tmp_path, now=NOW
        )

    assert rows[0]["n_proxy"] == 1
    assert rows[0]["per_source"]["stories_inside_horizon"] == 1


# ── 3. Eine frische exakte Beobachtung dominiert den Proxy ───────────────────


@pytest.mark.asyncio
async def test_fresh_exact_observation_below_target_suppresses_the_proxy_alarm(
    session_factory, tmp_path: Path
) -> None:
    """Live-Form 2026-08-02: Proxy ÜBER dem Ziel, exakte Messung DARUNTER."""
    async with session_factory.begin() as session:
        session.add_all(
            [
                _doc(1, symbol="ETH/USDT", when=NOW - timedelta(days=5)),
                _doc(2, symbol="SOL/USDT", when=NOW - timedelta(days=4)),
                _doc(3, symbol="ADA/USDT", when=NOW - timedelta(days=3)),
            ]
        )
    _observe(
        tmp_path,
        [
            {
                "prereg_id": "b20ef1487ccba99d",
                "observed_at_utc": (NOW - timedelta(hours=2)).isoformat(),
                "n_exact": 2,
                "n_target": 3,
            }
        ],
    )
    async with session_factory() as session:
        rows = await compute_maturity(
            session, specs=(_spec(n_target=3),), artifacts_dir=tmp_path, now=NOW
        )

    row = rows[0]
    assert row["state"] == STATE_NOT_DUE
    assert row["due"] is False
    assert row["n_exact"] == 2
    assert row["n_proxy"] == 3  # der Proxy erreicht das Ziel — er zählt nicht mehr
    assert row["state_source"] == "exact_observation"


@pytest.mark.asyncio
async def test_observation_measured_against_another_target_is_rejected(
    session_factory, tmp_path: Path
) -> None:
    """Ein anderes ``n_target`` heißt: gegen eine ANDERE Latte gemessen."""
    async with session_factory.begin() as session:
        session.add_all([_doc(1, symbol="ETH/USDT", when=NOW - timedelta(days=5))])
    _observe(
        tmp_path,
        [
            {
                "prereg_id": "b20ef1487ccba99d",
                "observed_at_utc": (NOW - timedelta(hours=2)).isoformat(),
                "n_exact": 0,
                "n_target": 9999,
            }
        ],
    )
    async with session_factory() as session:
        rows = await compute_maturity(
            session, specs=(_spec(n_target=1),), artifacts_dir=tmp_path, now=NOW
        )

    assert rows[0]["state_source"] == "proxy"
    assert rows[0]["state"] == STATE_EVAL_CHECK_DUE


@pytest.mark.asyncio
async def test_fresh_exact_observation_at_target_is_judgeable(
    session_factory, tmp_path: Path
) -> None:
    _observe(
        tmp_path,
        [
            {
                "prereg_id": "b20ef1487ccba99d",
                "observed_at_utc": (NOW - timedelta(hours=1)).isoformat(),
                "n_exact": 300,
                "n_target": 300,
            }
        ],
    )
    async with session_factory() as session:
        rows = await compute_maturity(
            session, specs=(_spec(n_target=300),), artifacts_dir=tmp_path, now=NOW
        )

    assert rows[0]["state"] == STATE_JUDGEABLE
    assert rows[0]["state_source"] == "exact_observation"


@pytest.mark.asyncio
async def test_stale_exact_observation_hands_back_to_the_proxy(
    session_factory, tmp_path: Path
) -> None:
    """Eine alte Messung darf den Alarm nicht dauerhaft stummschalten."""
    async with session_factory.begin() as session:
        session.add_all(
            [
                _doc(1, symbol="ETH/USDT", when=NOW - timedelta(days=9)),
                _doc(2, symbol="SOL/USDT", when=NOW - timedelta(days=8)),
            ]
        )
    _observe(
        tmp_path,
        [
            {
                "prereg_id": "b20ef1487ccba99d",
                "observed_at_utc": (
                    NOW - timedelta(days=EXACT_OBSERVATION_MAX_AGE_DAYS + 1)
                ).isoformat(),
                "n_exact": 10,
                "n_target": 300,
            }
        ],
    )
    async with session_factory() as session:
        rows = await compute_maturity(
            session, specs=(_spec(n_target=2),), artifacts_dir=tmp_path, now=NOW
        )

    assert rows[0]["state"] == STATE_EVAL_CHECK_DUE
    assert rows[0]["state_source"] == "proxy"
    assert rows[0]["n_exact"] is None


@pytest.mark.asyncio
async def test_newest_observation_wins_and_other_ids_are_ignored(
    session_factory, tmp_path: Path
) -> None:
    _observe(
        tmp_path,
        [
            {
                "prereg_id": "b20ef1487ccba99d",
                "observed_at_utc": (NOW - timedelta(hours=30)).isoformat(),
                "n_exact": 247,
                "n_target": 300,
            },
            {
                "prereg_id": "ffffffffffffffff",
                "observed_at_utc": (NOW - timedelta(minutes=5)).isoformat(),
                "n_exact": 999,
                "n_target": 300,
            },
            {
                "prereg_id": "b20ef1487ccba99d",
                "observed_at_utc": (NOW - timedelta(hours=2)).isoformat(),
                "n_exact": 273,
                "n_target": 300,
            },
        ],
    )
    async with session_factory() as session:
        rows = await compute_maturity(
            session, specs=(_spec(n_target=300),), artifacts_dir=tmp_path, now=NOW
        )

    assert rows[0]["n_exact"] == 273


@pytest.mark.asyncio
async def test_missing_observation_file_keeps_the_proxy_behaviour(
    session_factory, tmp_path: Path
) -> None:
    async with session_factory.begin() as session:
        session.add_all([_doc(1, symbol="ETH/USDT", when=NOW - timedelta(days=5))])
    async with session_factory() as session:
        rows = await compute_maturity(
            session, specs=(_spec(n_target=1),), artifacts_dir=tmp_path, now=NOW
        )

    assert rows[0]["state"] == STATE_EVAL_CHECK_DUE
    assert rows[0]["state_source"] == "proxy"


# ── Schreiber: das n kommt aus dem versiegelten Gate, nicht aus neuer Zählung ─

SEALED_GATE = {
    "level": "stories",
    "horizon_s": 86400,
    "n_min": 300,
    "p_min": 0.95,
    "require_cost_clearing": True,
    "max_top_symbol_share": 0.8,
}


def _eval_result(n: int) -> dict:
    return {
        "cost_bps": 37.22,
        "stories": {
            "n": n,
            "horizons": {
                "86400": {
                    "n": n,
                    "mean_bps": 9.41,
                    "p_positive": 0.16,
                    "cost_ref_bps": 37.22,
                    "top_symbol_share": 0.12,
                }
            },
        },
    }


def test_record_exact_observation_takes_n_from_the_sealed_gate(tmp_path: Path) -> None:
    rec = record_exact_observation(
        prereg_id="b20ef1487ccba99d",
        gate=SEALED_GATE,
        n_target=300,
        eval_result=_eval_result(273),
        artifacts_dir=tmp_path,
        observed_at=NOW,
        source_json="nd_v2_20260802.json",
    )

    assert rec["n_exact"] == 273
    assert rec["n_target"] == 300
    assert rec["gate_passed"] is False
    assert rec["horizon_s"] == 86400

    written = (tmp_path / "research" / "prereg_exact_observations.jsonl").read_text(
        encoding="utf-8"
    )
    assert json.loads(written.strip())["n_exact"] == 273


def test_record_exact_observation_refuses_when_the_judged_block_is_absent(
    tmp_path: Path,
) -> None:
    """Kein geurteilter Block = nichts gemessen. Eine erfundene 0 wäre schlimmer."""
    with pytest.raises(ValueError, match="not present"):
        record_exact_observation(
            prereg_id="b20ef1487ccba99d",
            gate=SEALED_GATE,
            n_target=300,
            eval_result={"pooled": {}},
            artifacts_dir=tmp_path,
            observed_at=NOW,
        )
    assert not (tmp_path / "research" / "prereg_exact_observations.jsonl").exists()


@pytest.mark.asyncio
async def test_written_observation_is_read_back_and_dominates(
    session_factory, tmp_path: Path
) -> None:
    """Ende-zu-Ende: schreiben → compute_maturity liest es und meldet NOT_DUE."""
    record_exact_observation(
        prereg_id="b20ef1487ccba99d",
        gate={**SEALED_GATE, "n_min": 3},
        n_target=3,
        eval_result=_eval_result(2),
        artifacts_dir=tmp_path,
        observed_at=NOW - timedelta(minutes=10),
    )
    async with session_factory.begin() as session:
        session.add_all(
            [
                _doc(1, symbol="ETH/USDT", when=NOW - timedelta(days=5)),
                _doc(2, symbol="SOL/USDT", when=NOW - timedelta(days=4)),
                _doc(3, symbol="ADA/USDT", when=NOW - timedelta(days=3)),
            ]
        )
    async with session_factory() as session:
        rows = await compute_maturity(
            session, specs=(_spec(n_target=3),), artifacts_dir=tmp_path, now=NOW
        )

    assert rows[0]["n_proxy"] == 3
    assert rows[0]["n_exact"] == 2
    assert rows[0]["state"] == STATE_NOT_DUE
