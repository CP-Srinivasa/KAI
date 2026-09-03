"""Was ``/health/payment`` beweisen muss (ADR 0018 §10).

Die Leitfrage ist nicht "laeuft der Dienst?", sondern **"ist die Aussage ueber
das Geld gedeckt?"**. Deshalb kennt dieser Schnappschuss kein gruenes Licht
ohne Beleg: eine gebrochene Journal-Kette oder ein Reconciler mit ``attention``
machen ihn ``degraded``, ganz gleich wie gesund der Node sich meldet.

Der zweite Punkt ist die Verschwiegenheit. Ein Health-Endpunkt ist die Stelle,
an der Betriebsdaten am ehesten in ein Ticket kopiert werden — hier darf
deshalb nichts stehen, was ein Ziel, ein Macaroon oder einen Preimage
zurueckspiegelt. ``live_gate`` traegt aus demselben Grund ausschliesslich
Booleans: WELCHE Datei fehlt, ist eine Frage fuer den Operator, nicht fuer eine
HTTP-Antwort.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core.lightning_settings import LightningSettings
from app.core.payment_settings import PaymentSettings
from app.payments.health import payment_health_snapshot
from app.payments.journal import PaymentJournal
from app.payments.rails.simulation import SimulationRail
from app.payments.reconcile_types import STATE_FILENAME, ReconcileState, save_state

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def _journal(tmp_path: Path) -> PaymentJournal:
    journal = PaymentJournal(tmp_path / "payments" / "payment_journal.jsonl")
    journal.open()
    return journal


async def _snapshot(tmp_path: Path, journal: PaymentJournal, **kwargs: object) -> dict:
    return await payment_health_snapshot(
        journal=journal,
        rail=kwargs.pop("rail", SimulationRail(now=NOW)),  # type: ignore[arg-type]
        settings=kwargs.pop("settings", PaymentSettings(mode="simulation")),  # type: ignore[arg-type]
        lightning=kwargs.pop("lightning", LightningSettings()),  # type: ignore[arg-type]
        app_env=kwargs.pop("app_env", "testing"),  # type: ignore[arg-type]
        now=kwargs.pop("now", NOW),  # type: ignore[arg-type]
        state_path=tmp_path / "payments" / STATE_FILENAME,
        **kwargs,  # type: ignore[arg-type]
    )


def _settled_intent(journal: PaymentJournal, intent_id: str, *, at: datetime, fee: int) -> None:
    journal.append(
        intent_id,
        "submitted",
        {"status": "SUBMITTED", "rail_dedup_key": "a" * 64, "amount_sent_minor_units": 1000},
        ts=at,
    )
    journal.append(
        intent_id,
        "settled",
        {
            "status": "SETTLED",
            "amount_settled_minor_units": 1000,
            "fee_actual_minor_units": fee,
            "proof_hash": "b" * 64,
        },
        ts=at + timedelta(seconds=2),
    )


# --------------------------------------------------------------------------- #
# Grundzustand
# --------------------------------------------------------------------------- #


async def test_ein_leeres_journal_ist_ok_und_bei_genesis(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    snapshot = await _snapshot(tmp_path, journal)

    assert snapshot["status"] == "ok"
    assert snapshot["mode"] == "simulation"
    assert snapshot["journal"]["chain"] == "ok"
    assert snapshot["journal"]["seq"] == 0
    assert snapshot["in_flight"] == 0


async def test_in_simulation_ist_der_rail_simuliert_und_nicht_befragt(tmp_path: Path) -> None:
    """Ein Health-Endpunkt darf keinen Node-Aufruf ausloesen, den niemand bestellt hat."""

    class Explodes(SimulationRail):
        async def health(self) -> object:  # pragma: no cover - darf nie gerufen werden
            raise AssertionError("SIMULATION must not query a node")

    journal = _journal(tmp_path)
    snapshot = await _snapshot(tmp_path, journal, rail=Explodes(now=NOW))

    assert snapshot["rail"]["name"] == "lightning"
    assert snapshot["rail"]["state"] == "simulated"


async def test_ein_stummer_node_macht_den_zustand_unbekannt_nicht_gruen(tmp_path: Path) -> None:
    class Silent(SimulationRail):
        async def health(self) -> object:
            raise RuntimeError("node unreachable")

    journal = _journal(tmp_path)
    snapshot = await _snapshot(
        tmp_path,
        journal,
        rail=Silent(now=NOW),
        settings=PaymentSettings(mode="shadow"),
    )

    assert snapshot["rail"]["state"] == "unknown"
    assert snapshot["rail"]["reachable"] is False


# --------------------------------------------------------------------------- #
# Kein Gruen ohne Beweis
# --------------------------------------------------------------------------- #


async def test_gebrochene_kette_macht_degraded(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    journal.append("pi_1", "intent_created", {"status": "REQUESTED"}, ts=NOW)
    journal.append("pi_1", "submitted", {"amount_sent_minor_units": 500}, ts=NOW)
    rows = [json.loads(line) for line in journal.path.read_text(encoding="utf-8").splitlines()]
    rows[1]["payload"]["amount_sent_minor_units"] = 1
    journal.path.write_text(
        "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in rows),
        encoding="utf-8",
    )

    snapshot = await _snapshot(tmp_path, PaymentJournal(journal.path))

    assert snapshot["status"] == "degraded"
    assert snapshot["journal"]["chain"] == "broken"


async def test_reconciler_mit_attention_macht_degraded(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    save_state(
        tmp_path / "payments" / STATE_FILENAME,
        ReconcileState(last_run_utc=NOW.isoformat(), last_status="attention", last_orphans=1),
    )

    snapshot = await _snapshot(tmp_path, journal)

    assert snapshot["status"] == "degraded"
    assert snapshot["reconciliation"]["status"] == "attention"
    assert snapshot["reconciliation"]["orphans"] == 1


# --------------------------------------------------------------------------- #
# Kennzahlen
# --------------------------------------------------------------------------- #


async def test_settlement_latenz_und_gebuehren_kommen_aus_dem_journal(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    _settled_intent(journal, "pi_1", at=NOW - timedelta(minutes=10), fee=3)
    _settled_intent(journal, "pi_2", at=NOW - timedelta(minutes=5), fee=7)

    snapshot = await _snapshot(tmp_path, journal)

    assert snapshot["settlement_latency_p50_ms"] == 2000.0
    assert snapshot["settlement_latency_p95_ms"] == 2000.0
    assert snapshot["fees_minor_units"] == 10
    assert snapshot["last_settlement"]["amount_minor_units"] == 1000
    assert snapshot["last_settlement"]["ts"].startswith("2026-09-03")


async def test_ohne_settlement_ist_die_latenz_unbekannt_nicht_null(tmp_path: Path) -> None:
    """``0.0`` waere eine Messung. ``None`` ist die Wahrheit bei n=0."""
    journal = _journal(tmp_path)
    snapshot = await _snapshot(tmp_path, journal)

    assert snapshot["settlement_latency_p50_ms"] is None
    assert snapshot["settlement_latency_p95_ms"] is None
    assert snapshot["last_settlement"] is None
    assert snapshot["last_failure"] is None


async def test_ablehnungen_und_fehler_werden_gezaehlt(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    journal.append(
        "pi_1",
        "policy_decided",
        {"verdict": "DENY", "rule_ids": ["amount_limits"], "status": "DENIED"},
        ts=NOW - timedelta(minutes=1),
    )
    journal.append(
        "pi_2",
        "policy_decided",
        {"verdict": "ALLOW", "status": "AUTHORIZED"},
        ts=NOW - timedelta(minutes=1),
    )
    journal.append(
        "pi_3",
        "failed",
        {"status": "FAILED_FINAL", "failure_reason": "NO_ROUTE"},
        ts=NOW - timedelta(minutes=2),
    )

    snapshot = await _snapshot(tmp_path, journal)

    assert snapshot["policy_reject_count"] == 1
    assert snapshot["last_failure"]["failure_class"] == "NO_ROUTE"


async def test_alte_ereignisse_liegen_ausserhalb_des_fensters(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    journal.append(
        "pi_1",
        "policy_decided",
        {"verdict": "DENY", "rule_ids": ["amount_limits"], "status": "DENIED"},
        ts=NOW - timedelta(days=3),
    )

    snapshot = await _snapshot(tmp_path, journal, window_hours=24.0)

    assert snapshot["policy_reject_count"] == 0


async def test_offene_sends_werden_gezaehlt(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    journal.append(
        "pi_1",
        "submitted",
        {"status": "SUBMITTED", "rail_dedup_key": "a" * 64, "amount_sent_minor_units": 100},
        ts=NOW,
    )
    journal.append(
        "pi_2",
        "reconciled",
        {"status": "RECONCILIATION_REQUIRED", "rail_dedup_key": "b" * 64},
        ts=NOW,
    )

    snapshot = await _snapshot(tmp_path, journal)

    assert snapshot["in_flight"] == 1
    assert snapshot["reconciliation_required"] == 1


# --------------------------------------------------------------------------- #
# LIVE-Tor und Verschwiegenheit
# --------------------------------------------------------------------------- #


async def test_live_gate_traegt_nur_booleans(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    snapshot = await _snapshot(tmp_path, journal)

    gate = snapshot["live_gate"]
    assert set(gate) == {"app_env_production", "pay_enabled", "hotp_seed_present", "fee_limit_ok"}
    assert all(isinstance(value, bool) for value in gate.values())
    assert gate["app_env_production"] is False


async def test_kein_pfad_und_kein_geheimnis_im_schnappschuss(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    _settled_intent(journal, "pi_1", at=NOW, fee=1)
    lightning = LightningSettings(
        macaroon_path="/secret/place/readonly.macaroon",
        hotp_seed_path="/secret/place/hotp.b32",
    )

    snapshot = await _snapshot(tmp_path, journal, lightning=lightning)

    blob = json.dumps(snapshot, sort_keys=True)
    assert "/secret/place" not in blob
    assert "macaroon" not in blob
    assert "b" * 64 not in blob, "auch ein Proof-Hash gehoert nicht in einen Health-Body"
