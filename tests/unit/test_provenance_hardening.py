"""Stage 2.1: die Provenienz-Felder müssen bedeuten, was sie behaupten.

Der Verifier wird diese Felder als Wahrheit behandeln. Drei Uneindeutigkeiten
aus #743 werden deshalb hier festgenagelt, bevor sie zur nächsten „wir hatten
das Feld, aber es bedeutete etwas anderes"-Forensik führen:

1. die Tick-Klammer leckte bei jedem Early Return,
2. ``is_stale`` wurde gesammelt und dann verworfen,
3. ``raw_market_price`` beschrieb bei Liquidationen einen *anderen* Preis als
   die übrigen Provenienz-Felder.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.execution.models import PriceEvidence
from app.execution.paper_engine import PaperExecutionEngine

EV = PriceEvidence(
    source="bybit",
    observed_at_utc="2026-08-21T09:15:00+00:00",
    observed_price=120.0,
    age_ms=1400.0,
    is_stale=False,
)


@pytest.fixture
def engine(tmp_path: Path) -> PaperExecutionEngine:
    return PaperExecutionEngine(
        initial_equity=10_000.0,
        fee_pct=0.1,
        slippage_pct=0.05,
        live_enabled=False,
        audit_log_path=str(tmp_path / "audit.jsonl"),
    )


def _closed(engine: PaperExecutionEngine) -> dict:
    path = Path(engine._audit_path)  # noqa: SLF001
    rows = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    closes = [r for r in rows if r.get("event_type") == "position_closed"]
    assert closes, "kein position_closed"
    return closes[-1]


def _open_long(engine: PaperExecutionEngine, symbol: str = "AAA/USDT", **kw) -> None:
    order = engine.create_order(
        symbol=symbol,
        side="buy",
        quantity=10.0,
        stop_loss=kw.get("stop_loss", 90.0),
        take_profit=kw.get("take_profit", 110.0),
        idempotency_key=f"open_{symbol}",
        leverage=kw.get("leverage", 1.0),
    )
    assert engine.fill_order(order, current_price=kw.get("price", 100.0)) is not None


# --- 1) Die Tick-Klammer darf bei KEINEM Ausgang lecken ------------------------


def test_tick_id_leckt_nicht_bei_blockierten_mutationen(
    engine: PaperExecutionEngine, monkeypatch
) -> None:
    """Der Early Return sprang am Cleanup vorbei — ein Entry erbte die Tick-Id."""
    _open_long(engine)
    monkeypatch.setattr(
        engine, "_mutations_blocked_reason", lambda: "paper_writer_frozen", raising=False
    )
    engine.monitor_positions(
        {"AAA/USDT": 120.0}, {"AAA/USDT": "bybit"}, price_evidence={"AAA/USDT": EV}
    )
    monkeypatch.undo()

    order = engine.create_order(
        symbol="BBB/USDT",
        side="buy",
        quantity=1.0,
        stop_loss=90.0,
        take_profit=110.0,
        idempotency_key="entry_after_block",
    )
    fill = engine.fill_order(order, current_price=100.0)
    assert fill is not None
    assert fill.monitor_tick_id == "", "Tick-Id aus einem fremden Lauf geerbt"
    assert fill.price_source == ""


def test_tick_id_leckt_nicht_bei_einer_exception(engine: PaperExecutionEngine, monkeypatch) -> None:
    _open_long(engine)

    def boom(*_a, **_k):
        raise RuntimeError("Trigger-Pfad kaputt")

    monkeypatch.setattr(engine, "check_stop_take", boom, raising=False)
    with pytest.raises(RuntimeError):
        engine.monitor_positions(
            {"AAA/USDT": 105.0}, {"AAA/USDT": "bybit"}, price_evidence={"AAA/USDT": EV}
        )
    monkeypatch.undo()

    order = engine.create_order(
        symbol="CCC/USDT",
        side="buy",
        quantity=1.0,
        stop_loss=90.0,
        take_profit=110.0,
        idempotency_key="entry_after_boom",
    )
    fill = engine.fill_order(order, current_price=100.0)
    assert fill is not None
    assert fill.monitor_tick_id == ""


# --- 2) is_stale wird gesammelt — also muss es auch ankommen -------------------


def test_stale_flag_wird_persistiert(engine: PaperExecutionEngine) -> None:
    """`source=bybit, age unbekannt, stale=true` ist eine andere Lage als stale=false."""
    _open_long(engine)
    engine.monitor_positions(
        {"AAA/USDT": 120.0},
        {"AAA/USDT": "bybit"},
        price_evidence={"AAA/USDT": PriceEvidence(source="bybit", is_stale=True)},
    )
    assert _closed(engine)["market_data_is_stale"] is True


def test_fehlendes_stale_flag_bleibt_none(engine: PaperExecutionEngine) -> None:
    _open_long(engine)
    engine.monitor_positions({"AAA/USDT": 120.0}, {"AAA/USDT": "bybit"})
    assert _closed(engine)["market_data_is_stale"] is None


# --- 3) Beobachteter Preis ≠ Ausführungs-Referenzpreis ------------------------


def test_liquidation_trennt_beobachteten_und_referenzpreis(engine: PaperExecutionEngine) -> None:
    """Bei Liquidation füllt die Engine gegen den berechneten Liq-Preis.

    Die übrigen Provenienz-Felder beschreiben aber den *beobachteten* Snapshot.
    Beides in ein Feld zu werfen, hätte dem Verifier zwei verschiedene Preise
    unter einem Namen untergeschoben.
    """
    _open_long(engine, leverage=5.0, price=100.0, stop_loss=50.0, take_profit=500.0)
    observed = 79.0
    engine.monitor_positions(
        {"AAA/USDT": observed},
        {"AAA/USDT": "bybit"},
        price_evidence={"AAA/USDT": PriceEvidence(source="bybit", observed_price=observed)},
    )
    row = _closed(engine)
    assert row["reason"] == "liquidation"
    assert row["observed_market_price"] == observed
    # Gefüllt wurde gegen den Liquidationspreis, nicht gegen den Marktpreis.
    assert row["execution_reference_price"] != observed
    assert row["exit_price"] == row["execution_reference_price"] * (1 - 0.0005)


def test_normalfall_beide_preise_identisch(engine: PaperExecutionEngine) -> None:
    _open_long(engine)
    engine.monitor_positions(
        {"AAA/USDT": 120.0}, {"AAA/USDT": "bybit"}, price_evidence={"AAA/USDT": EV}
    )
    row = _closed(engine)
    assert row["observed_market_price"] == 120.0
    assert row["execution_reference_price"] == 120.0


# --- 4) Alter ehrlich, nicht scheingenau ---------------------------------------


def test_age_wird_beim_fuellen_gemessen_nicht_beim_sammeln(engine: PaperExecutionEngine) -> None:
    """Zwischen Sammeln und Füllen vergeht Zeit — das Feld muss das abbilden.

    Der Zeitstempel wird relativ zu `now` gebaut, damit der Test nicht irgendwann
    an einem festen Datum kippt (Lehre: Zeitbomben-Tests brauchen injiziertes now).
    """
    _open_long(engine)
    observed = (datetime.now(UTC) - timedelta(seconds=3)).isoformat()
    engine.monitor_positions(
        {"AAA/USDT": 120.0},
        {"AAA/USDT": "bybit"},
        price_evidence={
            "AAA/USDT": PriceEvidence(source="bybit", observed_at_utc=observed, age_ms=1400.0)
        },
    )
    row = _closed(engine)
    # Aus filled_at - price_observed_at_utc, nicht aus dem Sammel-Wert 1400.
    assert row["market_data_age_ms"] != 1400.0
    assert 2_000 < row["market_data_age_ms"] < 60_000
    assert row["market_data_age_ms_at_collection"] == 1400.0


def test_beobachtungszeit_aus_der_zukunft_gibt_none(engine: PaperExecutionEngine) -> None:
    """Eine Quote, die nach dem Fuellen beobachtet wurde, ist keine Evidenz."""
    _open_long(engine)
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    engine.monitor_positions(
        {"AAA/USDT": 120.0},
        {"AAA/USDT": "bybit"},
        price_evidence={"AAA/USDT": PriceEvidence(source="bybit", observed_at_utc=future)},
    )
    assert _closed(engine)["market_data_age_ms"] is None


def test_unparsebare_beobachtungszeit_gibt_none(engine: PaperExecutionEngine) -> None:
    _open_long(engine)
    engine.monitor_positions(
        {"AAA/USDT": 120.0},
        {"AAA/USDT": "bybit"},
        price_evidence={"AAA/USDT": PriceEvidence(source="bybit", observed_at_utc="kaputt")},
    )
    assert _closed(engine)["market_data_age_ms"] is None


@pytest.mark.parametrize("bad", [float("nan"), math.inf, -math.inf, -5.0])
def test_nicht_endliche_alter_gelangen_nie_ins_audit(
    engine: PaperExecutionEngine, bad: float
) -> None:
    """Kein NaN/Inf/negatives Alter im Audit — fail-closed auf None."""
    _open_long(engine)
    engine.monitor_positions(
        {"AAA/USDT": 120.0},
        {"AAA/USDT": "bybit"},
        price_evidence={"AAA/USDT": PriceEvidence(source="bybit", age_ms=bad)},
    )
    value = _closed(engine)["market_data_age_ms_at_collection"]
    assert value is None


# --- 5) Der neue Identifier soll dauerhaft tragen ------------------------------


def test_tick_id_nutzt_die_volle_uuid(engine: PaperExecutionEngine) -> None:
    """48 Bit sind für eine dauerhafte Audit-Identität zu knapp."""
    _open_long(engine)
    fills = engine.monitor_positions(
        {"AAA/USDT": 120.0}, {"AAA/USDT": "bybit"}, price_evidence={"AAA/USDT": EV}
    )
    assert fills
    tick = fills[0].monitor_tick_id
    assert tick.startswith("tick_")
    assert len(tick) == len("tick_") + 32
