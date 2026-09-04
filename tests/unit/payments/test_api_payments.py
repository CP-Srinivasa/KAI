"""Die HTTP-Grenze des Geldpfads (ADR 0018 §10/§11).

Was hier geprueft wird, ist nicht der Dienst — der hat eigene Tests — sondern
die Grenze:

* **``Idempotency-Key`` ist Pflicht.** Ohne Header 400, nicht "dann erzeugen
  wir einen". Ein serverseitiger Key macht jeden Client-Retry zu einer zweiten
  Zahlung; genau dafuer existiert der Header.
* **Ein zweites ``execute`` sendet nicht.** Es antwortet mit dem Zustand, den
  der Aufrufer schon hat.
* **``/payments/*`` ist auch von 127.0.0.1 auth-pflichtig.** Der Bestand hat
  hier eine Falle (``dashboard_local``-Bypass); ein Test haelt fest, dass der
  Geldpfad NICHT hineinfaellt.
* **Kein Geheimnis in einer Antwort.** Weder Destination noch Idempotency-Key
  noch Preimage — die Antworten sollen ohne Nachdenken loggbar sein.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import payments as payments_router
from app.core.payment_settings import PaymentSettings
from app.payments.journal import PaymentJournal
from app.payments.rails.simulation import SimulationRail
from app.payments.service import PaymentService

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
DESTINATION = "sim:settle:alice"
KEY = "idem-key-0000000001"


class FakeHotp:
    def __init__(self, good_code: str = "123456") -> None:
        self.good_code = good_code
        self.calls: list[str] = []

    def verify(self, code: str) -> object:
        self.calls.append(code)
        if code != self.good_code:
            raise RuntimeError("HOTP verification failed")

        class Result:
            counter_used = 42

        return Result()


def _settings(**overrides: Any) -> PaymentSettings:
    base: dict[str, Any] = {
        "mode": "simulation",
        "destination_allowlist": hashlib.sha256(f"payee:{DESTINATION}".encode()).hexdigest(),
        "purposes_allowed": "self_test",
        "per_payment_max_sat": 5000,
        "daily_hard_cap_sat": 10_000,
        "approval_threshold_sat": 4000,
        "fee_limit_max_sat": 200,
    }
    base.update(overrides)
    return PaymentSettings(**base)


@pytest.fixture(autouse=True)
def _fresh_rate_limiter() -> None:
    payments_router._reset_rate_limiter_for_tests()


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    app = FastAPI()
    app.include_router(payments_router.router)
    rail = SimulationRail(now=NOW)
    journal = PaymentJournal(tmp_path / "payments" / "payment_journal.jsonl")
    journal.open()
    app.state.payment_service = PaymentService(
        journal=journal,
        rails={"simulation": rail, "lightning": rail},
        settings=_settings(),
        clock=lambda: NOW,
        hotp_verifier=FakeHotp(),
    )
    app.state.payment_rail = rail
    return TestClient(app)


def _body(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "actor": "operator",
        "purpose": "self_test",
        "destination": DESTINATION,
        "amount_sat": 1000,
        "fee_limit_sat": 10,
        "correlation_id": "corr-1",
    }
    base.update(overrides)
    return base


def _create(client: TestClient, key: str = KEY, **overrides: Any) -> dict[str, Any]:
    response = client.post(
        "/payments/intents", json=_body(**overrides), headers={"Idempotency-Key": key}
    )
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# Aufnahme
# --------------------------------------------------------------------------- #


def test_ein_intent_ohne_idempotency_key_wird_abgelehnt(client: TestClient) -> None:
    response = client.post("/payments/intents", json=_body())
    assert response.status_code == 400
    assert "Idempotency-Key" in response.json()["detail"]


def test_ein_zu_kurzer_idempotency_key_wird_abgelehnt(client: TestClient) -> None:
    response = client.post("/payments/intents", json=_body(), headers={"Idempotency-Key": "short"})
    assert response.status_code == 400


def test_ein_intent_wird_aufgenommen_und_bewertet(client: TestClient) -> None:
    body = _create(client)
    assert body["status"] == "AUTHORIZED"
    assert body["verdict"] == "ALLOW"
    assert body["replayed"] is False
    assert body["intent_id"].startswith("pi_")


def test_derselbe_key_wird_wiedergegeben_statt_wiederholt(client: TestClient) -> None:
    first = _create(client)
    second = _create(client)
    assert second["replayed"] is True
    assert second["intent_id"] == first["intent_id"]


def test_die_antwort_traegt_kein_geheimnis(client: TestClient) -> None:
    blob = str(_create(client))
    assert DESTINATION not in blob
    assert KEY not in blob


def test_ein_abgelehnter_intent_nennt_seine_regel(client: TestClient) -> None:
    body = _create(client, amount_sat=9999)
    assert body["status"] == "DENIED"
    assert body["rule_ids"] == ["amount_limits"]


# --------------------------------------------------------------------------- #
# Lesen, Vorschau, Audit
# --------------------------------------------------------------------------- #


def test_ein_intent_ist_abrufbar(client: TestClient) -> None:
    intent_id = _create(client)["intent_id"]
    response = client.get(f"/payments/intents/{intent_id}")
    assert response.status_code == 200
    assert response.json()["status"] == "AUTHORIZED"


def test_ein_unbekannter_intent_ist_404(client: TestClient) -> None:
    assert client.get("/payments/intents/pi_nope").status_code == 404


def test_simulate_liefert_eine_quote_und_sendet_nicht(client: TestClient) -> None:
    intent_id = _create(client)["intent_id"]
    response = client.post(f"/payments/intents/{intent_id}/simulate")
    assert response.status_code == 200
    body = response.json()
    assert body["quote"]["estimate_source"] == "simulation"
    assert body["status"] == "AUTHORIZED"
    assert client.get(f"/payments/intents/{intent_id}").json()["status"] == "AUTHORIZED"


def test_audit_zeigt_die_records_des_vorgangs(client: TestClient) -> None:
    intent_id = _create(client)["intent_id"]
    events = client.get("/payments/audit", params={"intent_id": intent_id}).json()["events"]
    assert [e["event_type"] for e in events] == ["intent_created", "policy_decided"]
    assert all(len(e["record_hash"]) == 64 for e in events)


# --------------------------------------------------------------------------- #
# Ausfuehren
# --------------------------------------------------------------------------- #


def test_execute_sendet_und_meldet_settled(client: TestClient) -> None:
    intent_id = _create(client)["intent_id"]
    response = client.post(f"/payments/intents/{intent_id}/execute", json={"hotp_code": ""})
    assert response.status_code == 200
    assert response.json()["status"] == "SETTLED"


def test_ein_zweites_execute_sendet_nicht(client: TestClient) -> None:
    rail = client.app.state.payment_rail  # type: ignore[attr-defined]
    calls: list[str] = []
    original = rail.pay

    async def counting(intent: Any, attempt: Any) -> Any:
        calls.append(attempt.rail_dedup_key)
        return await original(intent, attempt)

    rail.pay = counting  # type: ignore[method-assign]
    intent_id = _create(client)["intent_id"]
    client.post(f"/payments/intents/{intent_id}/execute", json={})
    second = client.post(f"/payments/intents/{intent_id}/execute", json={})

    assert len(calls) == 1
    assert second.json()["replayed"] is True


def test_ueber_der_schwelle_verlangt_execute_einen_hotp_code(client: TestClient) -> None:
    intent_id = _create(client, amount_sat=4500)["intent_id"]
    assert client.get(f"/payments/intents/{intent_id}").json()["status"] == "AWAITING_APPROVAL"

    without = client.post(f"/payments/intents/{intent_id}/execute", json={})
    assert without.status_code == 400
    assert "hotp_code" in without.json()["detail"]

    wrong = client.post(f"/payments/intents/{intent_id}/execute", json={"hotp_code": "000000"})
    assert wrong.status_code == 403

    good = client.post(f"/payments/intents/{intent_id}/execute", json={"hotp_code": "123456"})
    assert good.status_code == 200
    assert good.json()["status"] == "SETTLED"


def test_ein_abgelehnter_intent_laesst_sich_nicht_ausfuehren(client: TestClient) -> None:
    intent_id = _create(client, amount_sat=9999)["intent_id"]
    response = client.post(f"/payments/intents/{intent_id}/execute", json={})
    assert response.status_code == 409
    assert "DENIED" in response.json()["detail"]


# --------------------------------------------------------------------------- #
# Forderungen
# --------------------------------------------------------------------------- #


def test_eine_forderung_wird_ausgestellt_und_ist_abrufbar(client: TestClient) -> None:
    created = client.post(
        "/payments/invoices",
        json={"amount_sat": 1000, "purpose": "self_test", "order_ref": "order-9"},
    )
    assert created.status_code == 200
    ref = created.json()["ref_hash"]
    assert created.json()["order_ref"] == "order-9"

    status = client.get(f"/payments/invoices/{ref}")
    assert status.status_code == 200
    assert status.json()["settled"] is False


def test_die_antwort_traegt_die_zahlungsaufforderung(client: TestClient) -> None:
    """Ohne ``payment_request`` kann niemand bezahlen — die Forderung waere leer.

    Die BOLT11 ist kein Geheimnis, sondern die Aufforderung selbst: sie nennt
    Betrag, Empfaenger und Ablauf und wird genau dafuer weitergegeben. Ein
    ``ref_hash`` allein ist eine Quittungsnummer ohne Rechnung.
    """
    created = client.post(
        "/payments/invoices",
        json={"amount_sat": 1000, "purpose": "self_test", "order_ref": "order-10"},
    )
    assert created.status_code == 200, created.text
    payment_request = created.json()["payment_request"]
    assert isinstance(payment_request, str)
    assert payment_request != ""


def test_die_zahlungsaufforderung_steht_nicht_im_journal(
    client: TestClient, tmp_path: Path
) -> None:
    """Antwort ja, Journal nein — die Redaktionsgrenze bleibt, wo sie war.

    Das Journal traegt Hashes, weil es dauerhaft und exportierbar ist. Eine
    Zahlungsaufforderung darf durch die HTTP-Antwort gehen, ohne dass sie
    danach in jeder Journal-Zeile liegt.
    """
    created = client.post(
        "/payments/invoices",
        json={"amount_sat": 1000, "purpose": "self_test", "order_ref": "order-11"},
    )
    assert created.status_code == 200, created.text
    payment_request = created.json()["payment_request"]

    journal_text = (tmp_path / "payments" / "payment_journal.jsonl").read_text(encoding="utf-8")
    assert "order-11" in journal_text, "der Record muss ueberhaupt geschrieben worden sein"
    assert payment_request not in journal_text
    assert "payment_request" not in journal_text


def test_eine_forderung_laeuft_erst_nach_einer_stunde_ab(client: TestClient) -> None:
    """300 s reichen einem Menschen nicht: Wallet oeffnen, scannen, bezahlen."""
    created = client.post(
        "/payments/invoices",
        json={"amount_sat": 1000, "purpose": "self_test"},
    )
    assert created.status_code == 200, created.text
    expires_at = datetime.fromisoformat(created.json()["expires_at"])
    assert expires_at - NOW == timedelta(hours=1)


def test_eine_forderung_darf_nicht_laenger_als_einen_tag_leben(client: TestClient) -> None:
    """Eine unbezahlte Invoice belegt eine Zeile am Node; unbegrenzt ist keine Zahl."""
    created = client.post(
        "/payments/invoices",
        json={"amount_sat": 1000, "purpose": "self_test", "expiry_seconds": 86_401},
    )
    assert created.status_code == 422


# --------------------------------------------------------------------------- #
# Ohne Control Plane / Rate-Limit
# --------------------------------------------------------------------------- #


def test_ohne_control_plane_ist_der_pfad_zu() -> None:
    app = FastAPI()
    app.include_router(payments_router.router)
    response = TestClient(app).post(
        "/payments/intents", json=_body(), headers={"Idempotency-Key": KEY}
    )
    assert response.status_code == 503


def test_zu_viele_aufnahmen_werden_gebremst(client: TestClient) -> None:
    """Der Serialisierungspunkt darf nicht von einer Schleife belegt werden."""
    limit = payments_router._MUTATION_LIMIT.threshold
    last = None
    for index in range(limit + 1):
        last = client.post(
            "/payments/intents",
            json=_body(),
            headers={"Idempotency-Key": f"idem-key-{index:012d}"},
        )
    assert last is not None
    assert last.status_code == 429
    assert last.headers["Retry-After"]
