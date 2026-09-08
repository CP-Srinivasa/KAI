"""Die HTTP-Grenze von KAI PAY (D-CORE-006).

Nicht der Dienst — der hat eigene Tests — sondern die Grenze:

* **Aus heisst 404.** Nicht "200 mit leerer Liste": ein abgeschalteter Pfad,
  der antwortet, sieht aus wie ein funktionierender ohne Kunden.
* **``/pay/*`` ist auch von 127.0.0.1 auth-pflichtig.** Der Bestand hat hier
  eine Falle (``dashboard_local``-Bypass); ein Test haelt fest, dass die
  Produktschicht nicht hineinfaellt — genau wie ``/payments/*``.
* **Kein ``ref_hash`` in einer Antwort.** Der Schluessel, unter dem der Kern
  bucht, gehoert in den Audit-Pfad, nicht in eine Produktantwort.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import pay as pay_router
from tests.unit.pay.conftest import NOW, Harness


@pytest.fixture(autouse=True)
def _fresh_rate_limiter() -> None:
    pay_router._reset_rate_limiter_for_tests()


@pytest.fixture
def wired(tmp_path: Path) -> tuple[TestClient, Harness]:
    harness = Harness(tmp_path)
    app = FastAPI()
    app.include_router(pay_router.router)
    app.state.pay_service = harness.service
    return TestClient(app), harness


@pytest.fixture
def client(wired: tuple[TestClient, Harness]) -> TestClient:
    return wired[0]


@pytest.fixture
def disabled_client() -> TestClient:
    app = FastAPI()
    app.include_router(pay_router.router)
    app.state.pay_service = None
    return TestClient(app)


def _body(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "amount_sat": 1200,
        "description": "Beratung 30 Minuten",
        "reference": "ORDER-1",
    }
    base.update(overrides)
    return base


def _create(client: TestClient, **overrides: Any) -> dict[str, Any]:
    response = client.post("/pay/requests", json=_body(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# Aus
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/pay/requests"),
        ("get", "/pay/requests"),
        ("get", "/pay/requests/pay_000000000001"),
        ("get", "/pay/requests/pay_000000000001/receipt"),
        ("get", "/pay/health"),
    ],
)
def test_abgeschaltet_antwortet_jeder_endpunkt_mit_404(
    disabled_client: TestClient, method: str, path: str
) -> None:
    kwargs: dict[str, Any] = {"json": _body()} if method == "post" else {}
    response = getattr(disabled_client, method)(path, **kwargs)
    assert response.status_code == 404
    assert response.json()["detail"] == "kai pay disabled"


# --------------------------------------------------------------------------- #
# Anfordern
# --------------------------------------------------------------------------- #


def test_eine_forderung_kommt_mit_allem_zum_bezahlen_zurueck(client: TestClient) -> None:
    body = _create(client)
    assert body["payment_id"].startswith("pay_")
    assert body["status"] == "WAITING"
    assert body["amount_sat"] == 1200
    assert body["bolt11"]
    assert body["lightning_uri"] == f"lightning:{body['bolt11']}"
    assert body["expires_at"] == (NOW + timedelta(minutes=15)).isoformat()


def test_keine_antwort_verraet_den_ref_hash(client: TestClient, wired: Any) -> None:
    body = _create(client)
    entry = wired[1].store.get(body["payment_id"])
    assert entry is not None
    status = client.get(f"/pay/requests/{body['payment_id']}").json()
    assert entry.ref_hash not in str(body)
    assert entry.ref_hash not in str(status)


@pytest.mark.parametrize(
    "overrides",
    [
        {"amount_sat": 0},
        {"amount_sat": -1},
        {"description": ""},
        {"description": "x" * 141},
        {"reference": "r" * 65},
        {"expiry_seconds": 10},
        {"expiry_seconds": 100_000},
    ],
)
def test_das_schema_lehnt_unbrauchbare_eingaben_ab(
    client: TestClient, overrides: dict[str, Any]
) -> None:
    assert client.post("/pay/requests", json=_body(**overrides)).status_code == 422


def test_ein_http_webhook_wird_abgelehnt(client: TestClient) -> None:
    response = client.post("/pay/requests", json=_body(webhook_url="http://shop.example/hook"))
    assert response.status_code == 422
    assert "https" in response.json()["detail"]


def test_ein_zu_grosser_betrag_wird_abgelehnt(client: TestClient) -> None:
    response = client.post("/pay/requests", json=_body(amount_sat=2_000_000))
    assert response.status_code == 422
    assert "APP_PAY_MAX_AMOUNT_SAT" in response.json()["detail"]


def test_derselbe_idempotency_key_gibt_dieselbe_antwort(client: TestClient) -> None:
    headers = {"Idempotency-Key": "order-1-attempt"}
    first = client.post("/pay/requests", json=_body(), headers=headers).json()
    second = client.post("/pay/requests", json=_body(), headers=headers).json()
    assert first == second


def test_ohne_key_entsteht_eine_zweite_forderung(client: TestClient) -> None:
    assert _create(client)["payment_id"] != _create(client)["payment_id"]


# --------------------------------------------------------------------------- #
# Nachfragen
# --------------------------------------------------------------------------- #


def test_der_stand_wird_frisch_beim_kern_geholt(client: TestClient, wired: Any) -> None:
    harness: Harness = wired[1]
    body = _create(client)
    entry = harness.store.get(body["payment_id"])
    assert entry is not None

    waiting = client.get(f"/pay/requests/{body['payment_id']}").json()
    assert waiting["status"] == "WAITING"
    assert waiting["bolt11"] == body["bolt11"]

    harness.rail.settle(entry.ref_hash)
    settled = client.get(f"/pay/requests/{body['payment_id']}").json()
    assert settled["status"] == "SETTLED"
    assert settled["paid_amount_sat"] == 1200
    assert settled["paid_at"]
    assert settled["bolt11"] is None


def test_eine_unbekannte_id_ist_ein_404(client: TestClient) -> None:
    response = client.get("/pay/requests/pay_deadbeefdead")
    assert response.status_code == 404
    assert "unknown payment request" in response.json()["detail"]


def test_die_referenz_beantwortet_die_frage_ob_bezahlt(client: TestClient, wired: Any) -> None:
    harness: Harness = wired[1]
    body = _create(client, reference="ORDER-42")
    entry = harness.store.get(body["payment_id"])
    assert entry is not None

    open_answer = client.get("/pay/requests", params={"reference": "ORDER-42"}).json()
    assert open_answer["paid"] is False
    assert open_answer["payment_ids"] == [body["payment_id"]]
    assert open_answer["latest"]["status"] == "WAITING"

    harness.rail.settle(entry.ref_hash)
    client.get(f"/pay/requests/{body['payment_id']}")
    paid_answer = client.get("/pay/requests", params={"reference": "ORDER-42"}).json()
    assert paid_answer["paid"] is True


def test_die_liste_kommt_neueste_zuerst_und_gedeckelt(client: TestClient) -> None:
    ids = [_create(client, reference=f"ORDER-{i}")["payment_id"] for i in range(4)]
    listed = client.get("/pay/requests", params={"limit": 2}).json()["requests"]
    assert [entry["payment_id"] for entry in listed] == [ids[3], ids[2]]


def test_ein_unbrauchbares_limit_wird_abgelehnt(client: TestClient) -> None:
    assert client.get("/pay/requests", params={"limit": 0}).status_code == 422
    assert client.get("/pay/requests", params={"limit": 101}).status_code == 422


# --------------------------------------------------------------------------- #
# Beleg und Betrieb
# --------------------------------------------------------------------------- #


def test_der_beleg_zitiert_das_geldjournal(client: TestClient, wired: Any) -> None:
    harness: Harness = wired[1]
    body = _create(client)
    entry = harness.store.get(body["payment_id"])
    assert entry is not None
    harness.rail.settle(entry.ref_hash)

    receipt = client.get(f"/pay/requests/{body['payment_id']}/receipt").json()
    assert receipt["receipt_id"].startswith("rcpt_")
    assert receipt["paid_amount_sat"] == 1200
    assert receipt["audit"]["journal_seq"] > 0
    assert len(receipt["audit"]["record_hash"]) == 64

    text = client.get(f"/pay/requests/{body['payment_id']}/receipt", params={"format": "text"})
    assert text.headers["content-type"].startswith("text/plain")
    assert receipt["audit"]["record_hash"] in text.text


def test_ohne_geldeingang_gibt_es_keinen_beleg(client: TestClient) -> None:
    body = _create(client)
    response = client.get(f"/pay/requests/{body['payment_id']}/receipt")
    assert response.status_code == 404
    assert "no settlement record" in response.json()["detail"]


def test_health_meldet_betrieb_ohne_geldkennzahl(client: TestClient) -> None:
    _create(client)
    health = client.get("/pay/health").json()
    assert health == {
        "enabled": True,
        "open_requests": 1,
        "settled_total": 0,
        "last_settled_at": None,
        "poller_alive": False,
    }


def test_zu_viele_anfragen_werden_gebremst(client: TestClient) -> None:
    for _ in range(30):
        client.post("/pay/requests", json=_body())
    response = client.post("/pay/requests", json=_body())
    assert response.status_code == 429
    assert response.headers["Retry-After"]
