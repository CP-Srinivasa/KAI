"""Unit tests for out-of-sample maturity counting of open pre-registrations."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.research.prereg_maturity import (
    STATE_SUPERVISED,
    STATE_UNWATCHED,
    build_maturity_alert,
    compute_maturity,
    find_unwatched_preregs,
)
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

    # Frist-Prae-Regs (kind="deadline") haengen NICHT am Datenbestand: sie
    # werden mit Ablauf ihres Fensters faellig, auch bei leerem Store — das ist
    # ihr Zweck. Dieser Test lief bis zum 2026-08-10 gruen und kippte in dem
    # Moment, als die Analyst-Probe f0e1a3a8 ihr window_end_utc erreichte
    # (00:13 UTC). Eine Zeitbombe, kein Regress: die Zeile IST korrekt faellig.
    # Geprueft wird deshalb nur, was vom leeren Store abhaengt.
    zaehl_rows = [r for r in rows if r.get("kind") != "deadline"]
    assert zaehl_rows, "Ohne zaehlbasierte Prae-Regs prueft dieser Test nichts mehr"
    assert all(r["due"] is False and r["n_proxy"] == 0 for r in zaehl_rows)

    # Die Frist-Zeilen muessen weiterhin erscheinen — nur ihre Faelligkeit
    # richtet sich nach der Uhr, nicht nach dem Store.
    for row in rows:
        if row.get("kind") == "deadline":
            assert row["n_proxy"] == 0


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
    assert drift["per_source"] == {"stories": 2, "stories_inside_horizon": 0, "events": 4}
    assert drift["n_proxy"] == 2  # Story-Level, NICHT 4 Events
    assert drift["due"] is True
    assert drift["state_source"] == "proxy"


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
            # Pflichtfeld seit WP2 (Audit 2026-08-06): Gate-Horizont explizit,
            # ein Spec ohne Horizont scheitert laut statt still per Default.
            "gate_horizon_s": 604800,
        },
        {
            "name": "exec_like",
            "kind": "exec_translation",
            "since_utc": reg,
            "n_target": 1,
            "gate_horizon_s": 86400,
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


@pytest.mark.asyncio
async def test_states_proxy_caps_at_eval_check_due(session_factory, tmp_path) -> None:
    """P0-01: Der Dokumenten-Proxy ist eine Obergrenze — er darf hoechstens
    EVAL_CHECK_DUE melden, niemals JUDGEABLE. Nur die Datei-Kinds (exakter
    Evaluator) duerfen JUDGEABLE erreichen."""
    import json as _json

    from app.research.prereg_maturity import (
        STATE_EVAL_CHECK_DUE,
        STATE_JUDGEABLE,
        STATE_NOT_DUE,
    )

    async with session_factory.begin() as session:
        session.add_all(
            [
                _doc(1, source="coindesk", tickers=["ETH/USDT"], when="2026-07-03T00:00:00"),
                _doc(2, source="coindesk", tickers=["SOL/USDT"], when="2026-07-04T00:00:00"),
            ]
        )

    art = tmp_path / "artifacts"
    art.mkdir()
    reg = "2026-07-29T09:14:47+00:00"
    (art / "paper_execution_audit.jsonl").write_text(
        _json.dumps(
            {
                "event_type": "order_filled",
                "document_id": "technical_paper_A",
                "filled_at": "2026-07-29T12:00:00+00:00",
                "timestamp_utc": "2026-07-29T12:00:00+00:00",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (art / "alert_outcomes.jsonl").write_text(
        _json.dumps({"document_id": "technical_paper_A", "outcome": "hit", "annotated_at": reg})
        + "\n",
        encoding="utf-8",
    )

    specs = (
        {
            "name": "proxy_reached",
            "prereg_id": "aaaa000011112222",
            "kind": "documents",
            "since_utc": "2026-07-02",
            "sources": None,
            "exclude_first_ticker": "BTC/USDT",
            "n_target": 2,
        },
        {
            "name": "proxy_not_reached",
            "kind": "documents",
            "since_utc": "2026-07-02",
            "sources": None,
            "exclude_first_ticker": "BTC/USDT",
            "n_target": 99,
        },
        {
            "name": "exact_reached",
            "prereg_id": "bbbb000011112222",
            "kind": "tech_precision",
            "since_utc": reg,
            "n_target": 1,
            "gate_horizon_s": 604800,
        },
    )
    async with session_factory() as session:
        rows = await compute_maturity(session, specs=specs, artifacts_dir=art)
    by = {r["name"]: r for r in rows}

    assert by["proxy_reached"]["state"] == STATE_EVAL_CHECK_DUE
    assert by["proxy_reached"]["due"] is True  # Kompat-Bit
    assert by["proxy_reached"]["prereg_id"] == "aaaa000011112222"
    assert by["proxy_not_reached"]["state"] == STATE_NOT_DUE
    assert by["proxy_not_reached"]["due"] is False
    assert by["exact_reached"]["state"] == STATE_JUDGEABLE


# ---------------------------------------------------------------------------
# Aufsichtsregister im Reifeblick — Befund 2026-08-31
#
# ``find_unwatched_preregs`` klassifiziert ein zweites Mal selbst; sie speist
# den taeglichen Operator-Alarm. Haette ich nur ``classify_ledger_entries``
# repariert, kassierte diese Kopie die Reparatur ([[feedback_duplicated_
# invariants_drift]]): der Alarm haette ``6751bc33`` weiter als
# Aufsichtsluecke gemeldet, waehrend die Ledger-Sicht SUPERVISED zeigt.
# ---------------------------------------------------------------------------


def _register(path: Path, *entries: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schema": "prereg_supervision/v1", "entries": list(entries)}),
        encoding="utf-8",
    )
    return path


def _sealed(root: Path, prereg_id: str, name: str) -> None:
    path = root / "research" / "prereg_ledger.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "schema": "prereg/v1",
                    "prereg_id": prereg_id,
                    "name": name,
                    "direction": "neutral",
                    "horizon": "24h",
                    "success_criteria": "irrelevant fuer diesen Test",
                    "sample_size_target": 100,
                    "created_at_utc": "2026-07-01T12:32:46+00:00",
                }
            )
            + "\n"
        )


_SUP_ID = "6751bc3364d39ec2"
_SUP_NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def test_a_supervised_claim_is_not_reported_as_an_oversight_gap(tmp_path: Path) -> None:
    _sealed(tmp_path, _SUP_ID, "sec_filing_timing")
    reg = _register(
        tmp_path / "config" / "prereg_supervision.json",
        {
            "prereg_id": _SUP_ID,
            "decision_state": "MANUAL_SCHEDULED_REVIEW",
            "owner": "operator",
            "next_review_utc": "2026-09-15T00:00:00+00:00",
        },
    )
    (row,) = find_unwatched_preregs(tmp_path, specs=(), supervision_register=reg, now=_SUP_NOW)
    assert row["state"] == STATE_SUPERVISED
    assert row["due"] is False
    assert "Keine Aufsichtsluecke" in row["note"]
    assert "MANUAL_SCHEDULED_REVIEW" in row["note"]


def test_without_the_register_it_is_an_oversight_gap_again(tmp_path: Path) -> None:
    """Positivkontrolle: der Zustand kommt aus dem Register, nicht aus Nachsicht."""
    _sealed(tmp_path, _SUP_ID, "sec_filing_timing")
    (row,) = find_unwatched_preregs(
        tmp_path,
        specs=(),
        supervision_register=tmp_path / "config" / "absent.json",
        now=_SUP_NOW,
    )
    assert row["state"] == STATE_UNWATCHED
    assert row["due"] is True


def test_an_overdue_supervised_claim_still_nags(tmp_path: Path) -> None:
    """Das Register darf keine Frist verschlucken — nur eine falsche Anklage."""
    _sealed(tmp_path, _SUP_ID, "sec_filing_timing")
    reg = _register(
        tmp_path / "config" / "prereg_supervision.json",
        {
            "prereg_id": _SUP_ID,
            "decision_state": "MANUAL_IMMEDIATE_VERDICT",
            "owner": "operator",
            "next_review_utc": "DUE_NOW",
        },
    )
    (row,) = find_unwatched_preregs(tmp_path, specs=(), supervision_register=reg, now=_SUP_NOW)
    assert row["state"] == STATE_SUPERVISED
    assert row["due"] is True
    alert = build_maturity_alert([row])
    assert alert is not None
    assert STATE_SUPERVISED in alert
    # Der Alarm nennt den Zustand ausdruecklich als NICHT-Luecke; eine blosse
    # Abwesenheit des Wortes waere schwaecher und liesse Schweigen durchgehen.
    assert "keine Aufsichtsluecke" in alert


def test_the_alert_never_calls_a_supervised_claim_unobserved(tmp_path: Path) -> None:
    """Der alte Text behauptete 'in KEINER Wachliste' — das war schlicht unwahr."""
    _sealed(tmp_path, _SUP_ID, "sec_filing_timing")
    reg = _register(
        tmp_path / "config" / "prereg_supervision.json",
        {
            "prereg_id": _SUP_ID,
            "decision_state": "MANUAL_IMMEDIATE_VERDICT",
            "owner": "operator",
            "next_review_utc": "DUE_NOW",
        },
    )
    rows = find_unwatched_preregs(tmp_path, specs=(), supervision_register=reg, now=_SUP_NOW)
    alert = build_maturity_alert(rows) or ""
    assert "in KEINER Wachliste" not in alert
    assert "unbeobachtet" not in alert


# ── Ueberfaelligkeit sichtbar machen (Befund 2026-08-31) ────────────────────
#
# K1 (00c75a76) meldete vom 2026-08-03 bis zum 2026-08-31 **taeglich denselben
# Satz**: "Fenster endete 2026-08-03T12:51:11 -> EVAL_CHECK_DUE". Am ersten und
# am achtundzwanzigsten Tag identisch. Eine Frist ohne sichtbares Alter ist
# eine Erinnerung, kein Druck — und genau deshalb fiel nicht auf, dass der
# Claim festhaengt.


def test_ein_geschlossenes_fenster_traegt_sein_alter(tmp_path: Path) -> None:
    from app.research.prereg_maturity import _maturity_deadline

    spec = {"window_end_utc": "2026-08-03T12:51:11.469459+00:00"}
    _n, detail, state = _maturity_deadline(spec, datetime(2026, 8, 31, 12, 0, tzinfo=UTC))

    assert state == "EVAL_CHECK_DUE"
    assert detail["days_overdue"] == 27
    assert detail["days_remaining"] == 0


def test_ein_offenes_fenster_ist_nicht_ueberfaellig(tmp_path: Path) -> None:
    from app.research.prereg_maturity import _maturity_deadline

    spec = {"window_end_utc": "2026-09-29T09:15:41+00:00"}
    _n, detail, state = _maturity_deadline(spec, datetime(2026, 8, 31, 12, 0, tzinfo=UTC))

    assert state == "NOT_DUE"
    assert detail["days_overdue"] == 0
    assert detail["days_remaining"] == 28


def test_der_alarm_nennt_das_alter_der_frist() -> None:
    row = {
        "name": "k1_channel_audit_resonance",
        "prereg_id": "00c75a76a2b0e78b",
        "kind": "deadline",
        "state": "EVAL_CHECK_DUE",
        "due": True,
        "window_end_utc": "2026-08-03T12:51:11+00:00",
        "per_source": {"window_end_utc": "2026-08-03T12:51:11+00:00", "days_overdue": 27},
    }
    alert = build_maturity_alert([row]) or ""

    assert "seit 27 Tagen" in alert


def test_ohne_ueberfaelligkeit_bleibt_der_text_unveraendert() -> None:
    """Positivkontrolle: der Zusatz erscheint nur, wenn die Frist wirklich zu ist."""
    row = {
        "name": "irgendein_claim",
        "prereg_id": "aaa",
        "kind": "deadline",
        "state": "EVAL_CHECK_DUE",
        "due": True,
        "window_end_utc": "2026-09-29T09:15:41+00:00",
        "per_source": {"window_end_utc": "2026-09-29T09:15:41+00:00", "days_overdue": 0},
    }
    alert = build_maturity_alert([row]) or ""

    assert "seit" not in alert.split("Fenster endete")[1][:40]
