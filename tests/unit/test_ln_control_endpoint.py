"""Die Kontrollflaeche ``POST /dashboard/api/ln/value-action`` nach dem Rueckbau.

Der Bestand pruefte hier eine ganze zweite Geldkette: Operator-Envelope,
Risikoklassen, Frisch-Balance-Gate, Tages-Cap aus dem v2-Journal,
Reserve-Boden, HOTP-Zeremonie und persistenten Idempotenz-Store. All das ist
mit ADR 0018 §12 (PR 1) entweder in den Payment Control Plane gewandert
(Regelkette inklusive ``reserve_floor``, Freigabeschwelle, Idempotenz am
Journal) oder mit dem alten Sendeweg gefallen.

Was hier bleibt, sind die Zusagen, die diese Flaeche noch SELBST traegt:

* nur zwei Aktionen sind erreichbar — ``pay_invoice`` (delegiert) und
  ``create_invoice`` (Empfangs-Gate);
* reservierte Gate-kwargs koennen nicht ueber ``params`` eingeschmuggelt werden;
* der Mint bleibt an den vorgeschauten Plan gebunden (keine Parameter-
  Substitution zwischen Vorschau und Ausfuehrung);
* der Mint bleibt inert, solange ``receive_enabled`` aus ist.

Die Zusagen des Sendepfads werden dort geprueft, wo er ist:
``tests/unit/payments/test_ln_control_delegation.py`` und
``tests/unit/payments/test_policy.py``.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import ln_control as lc

_URL = "/dashboard/api/ln/value-action"


def _app() -> FastAPI:
    a = FastAPI()
    a.include_router(lc.router)
    return a


def _post(body: dict[str, Any]) -> Any:
    return TestClient(_app()).post(_URL, json=body)


# --------------------------------------------------------------------------- #
# Register: nur noch zwei Aktionen
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("action", ["keysend", "send_coins", "open_channel", "close_channel"])
def test_the_deferred_actions_are_gone_not_denied(action: str) -> None:
    """ADR §1 fuehrt sie als DEFERRED — dann duerfen sie kein Menuepunkt sein.

    Sie standen im Register und wurden von der Policy abgelehnt. Ein Eintrag,
    der nur existiert, um abgelehnt zu werden, liest sich wie eine Faehigkeit,
    die man nur freischalten muesste.
    """
    r = _post({"action": action, "params": {}})
    assert r.status_code == 422
    assert "unknown action" in r.json()["detail"]


def test_unknown_action_is_422() -> None:
    assert _post({"action": "payout", "params": {}}).status_code == 422


def test_reserved_params_cannot_be_smuggled_in() -> None:
    """Ein injiziertes ``authorization`` schriebe eine LUEGE in den Audit-Trail."""
    r = _post(
        {
            "action": "create_invoice",
            "params": {"value_sat": 10, "authorization": {"policy_decision": "auto"}},
        }
    )
    assert r.status_code == 422
    assert "reserved params" in r.json()["detail"]


# --------------------------------------------------------------------------- #
# Mint: Plan-Bindung und Inertheit
# --------------------------------------------------------------------------- #


def test_plan_mode_previews_without_touching_the_node() -> None:
    body = _post({"action": "create_invoice", "params": {"value_sat": 1000}}).json()
    assert body["mode"] == "plan"
    assert len(body["plan_hash"]) == 64
    # receive_enabled ist in der Suite aus (conftest) → ehrlich ``disabled``.
    assert body["plan"]["state"] == "disabled"
    assert "receive_enabled" in body["plan"]["detail"]


def test_execute_with_a_stale_plan_hash_is_refused() -> None:
    """B-005-Kern: zwischen Vorschau und Ausfuehrung darf nichts ausgetauscht werden."""
    params = {"value_sat": 1000}
    plan = _post({"action": "create_invoice", "params": params}).json()
    r = _post(
        {
            "action": "create_invoice",
            "params": {"value_sat": 500_000},  # anderer Betrag, alter Hash
            "confirm": {"plan_hash": plan["plan_hash"], "idempotency_key": "k1"},
        }
    )
    assert r.status_code == 403
    assert "plan hash mismatch" in r.json()["detail"]


def test_execute_without_an_idempotency_key_is_refused() -> None:
    params = {"value_sat": 1000}
    plan = _post({"action": "create_invoice", "params": params}).json()
    r = _post(
        {
            "action": "create_invoice",
            "params": params,
            "confirm": {"plan_hash": plan["plan_hash"], "idempotency_key": ""},
        }
    )
    assert r.status_code == 403
    assert "idempotency key required" in r.json()["detail"]


def test_execute_stays_inert_while_receive_is_off() -> None:
    """Der Kill-Switch ist die aeussere Grenze, nicht die Zeremonie davor."""
    params = {"value_sat": 1000}
    plan = _post({"action": "create_invoice", "params": params}).json()
    body = _post(
        {
            "action": "create_invoice",
            "params": params,
            "confirm": {"plan_hash": plan["plan_hash"], "idempotency_key": "k1"},
        }
    ).json()
    assert body["mode"] == "execute"
    assert body["result"]["state"] == "disabled"
    assert "receive_enabled" in body["result"]["detail"]


def test_bad_params_for_the_action_are_422_not_500() -> None:
    """Ein Tippfehler in ``params`` ist eine Eingabe, kein Serverfehler.

    Er faellt schon in der Vorschau auf — der ``TypeError`` des Aufrufs wird zu
    422 uebersetzt, statt als 500 zu entkommen.
    """
    r = _post({"action": "create_invoice", "params": {"nonsense": 1}})
    assert r.status_code == 422
    assert "invalid params" in r.json()["detail"]
