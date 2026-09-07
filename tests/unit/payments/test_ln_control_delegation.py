"""Ein Sendeweg, nicht zwei (ADR 0018 §12).

Der Bestand hatte zwei vollstaendige Ketten zu demselben Node: das Cockpit
(``ln_control`` -> ``value_layer.pay_invoice``) mit eigener Policy, eigenem
Journal, eigenem Idempotenz-Store und eigenem Zustandsbegriff — und daneben
alles, was sonst noch ``client.pay_invoice`` rief. Zwei Wege heissen zwei
Meinungen ueber dieselbe Zahlung.

Der AST-Test ist der wichtigere der beiden hier: ein Verhaltenstest sagt, dass
die Delegation HEUTE greift; er sagt nicht, dass niemand morgen einen zweiten
Aufruf danebenschreibt. Die strukturelle Aussage haelt auch dann, wenn jemand
den zweiten Weg an einer Stelle einbaut, an die dieser Test nie sieht — weil
er ueber den ganzen Modulbaum laeuft, nicht ueber einen Aufruf.
"""

from __future__ import annotations

import ast
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import ln_control as lc
from app.api.routers import ln_control_delegate as delegate
from app.core.payment_settings import PaymentSettings
from app.payments.journal import PaymentJournal
from app.payments.rails.simulation import SimulationRail
from app.payments.service import PaymentService

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
URL = "/dashboard/api/ln/value-action"
DESTINATION = "sim:settle:alice"


# --------------------------------------------------------------------------- #
# Struktur: kein zweiter Sendeweg
# --------------------------------------------------------------------------- #


def _attribute_calls(module_path: Path) -> set[str]:
    """Alle ``x.y(...)``-Aufrufe eines Moduls als ``"x.y"``."""
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            found.add(f"{func.value.id}.{func.attr}")
    return found


def test_ln_control_ruft_den_value_layer_nicht_mehr_zum_zahlen() -> None:
    calls = _attribute_calls(Path(lc.__file__))
    assert "vl.pay_invoice" not in calls, (
        "ln_control darf nicht mehr direkt senden — der Weg fuehrt ueber den "
        "Payment Control Plane (ADR 0018 §12)"
    )


def test_der_value_layer_existiert_nicht_mehr() -> None:
    """Der Stolperdraht ist durch die Loeschung ersetzt (ADR 0018 §12, PR 1).

    ``delegate.legacy_pay_invoice_moved`` war eine Funktion, die laut wurde,
    falls jemand die Abzweigung entfernt und wieder auf
    ``value_layer.pay_invoice`` zeigt. Sie ist entfallen, weil es das Ziel
    nicht mehr gibt: ein zweiter Sendeweg laesst sich nicht mehr versehentlich
    wiederherstellen, er muesste neu geschrieben werden.
    """
    with pytest.raises(ModuleNotFoundError):
        import app.lightning.value_layer  # noqa: F401


def test_das_register_kennt_nur_noch_zwei_aktionen() -> None:
    """``keysend``/``send_coins``/``open_channel``/``close_channel`` sind DEFERRED.

    Sie standen im Register, obwohl die Regelkette des Control Plane sie mit
    ``unsupported_action`` abgelehnt haette. Ein Menuepunkt, der nur existiert,
    um abgelehnt zu werden, sieht aus wie eine Faehigkeit.
    """
    assert lc.ACTIONS == {"pay_invoice", "create_invoice"}


def test_der_modul_kommentar_behauptet_den_bypass_nicht_mehr() -> None:
    """SENTR: der Hinweis "a separate PR" las sich, als sei die Kontrollflaeche
    lokal noch offen — die S-001-Haertung ist laengst in ``auth.py``."""
    source = Path(lc.__file__).read_text(encoding="utf-8")
    assert "The S-001 local-bypass hardening is a separate PR" not in source
    assert "_requires_strong_auth" in source


# --------------------------------------------------------------------------- #
# Verhalten: die Zeremonie bleibt, der Sender wechselt
# --------------------------------------------------------------------------- #


class FakeHotp:
    def verify(self, code: str) -> object:
        if code != "123456":
            raise RuntimeError("HOTP verification failed")

        class Result:
            counter_used = 7

        return Result()


def _app(tmp_path: Path, **overrides: Any) -> FastAPI:
    app = FastAPI()
    app.include_router(lc.router)
    rail = SimulationRail(now=NOW)
    journal = PaymentJournal(tmp_path / "payments" / "payment_journal.jsonl")
    journal.open()
    base: dict[str, Any] = {
        "mode": "simulation",
        "destination_allowlist": hashlib.sha256(f"payee:{DESTINATION}".encode()).hexdigest(),
        "purposes_allowed": "operator_pay_invoice",
        "per_payment_max_sat": 100_000,
        "daily_hard_cap_sat": 100_000,
        "approval_threshold_sat": 90_000,
        "fee_limit_max_sat": 200,
    }
    base.update(overrides)
    app.state.payment_service = PaymentService(
        journal=journal,
        rails={"simulation": rail, "lightning": rail},
        settings=PaymentSettings(**base),
        clock=lambda: NOW,
        hotp_verifier=FakeHotp(),
    )
    app.state.payment_rail = rail
    return app


def _patch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nur noch EINE Stelle: der Betrag steckt sonst im BOLT11.

    Der Bestand brauchte hier fuenf Patches — Envelope, Cache-Balance,
    Frisch-Balance, Tages-Cap aus dem v2-Journal und den Journal-Blocker. Alle
    fuenf gehoerten zur zweiten Geldkette, die ``ln_control`` neben dem Control
    Plane fuehrte; mit ihr sind sie gegangen. Die Simulation kennt kein BOLT11,
    deshalb bleibt die Betragsableitung.
    """
    monkeypatch.setattr(lc, "bolt11_amount_sat", lambda _request: 1000)


def test_plan_mode_nennt_den_control_plane_als_weg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch(monkeypatch)
    client = TestClient(_app(tmp_path))
    body = client.post(
        URL, json={"action": "pay_invoice", "params": {"payment_request": DESTINATION}}
    ).json()

    assert body["mode"] == "plan"
    assert body["plan"]["route"] == "payment_control_plane"
    assert body["plan"]["mode"] == "simulation"
    assert body["plan"]["fee_limit_sat"] > 0


def test_execute_laeuft_durch_den_control_plane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch(monkeypatch)
    app = _app(tmp_path)
    client = TestClient(app)
    params = {"payment_request": DESTINATION}
    plan = client.post(URL, json={"action": "pay_invoice", "params": params}).json()

    response = client.post(
        URL,
        json={
            "action": "pay_invoice",
            "params": params,
            "confirm": {
                "hotp": "123456",
                "plan_hash": plan["plan_hash"],
                "idempotency_key": "cockpit-key-1",
            },
        },
    )

    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["route"] == "payment_control_plane"
    assert result["status"] == "SETTLED"
    # Der Vorgang steht im Geld-Journal, nicht im ops_ledger.
    events = app.state.payment_service.audit(result["intent_id"])
    assert [e.event_type for e in events][-1] == "settled"


def test_der_schluessel_wird_an_den_plan_gebunden() -> None:
    """Der Cockpit-Schluessel allein waere zu kurz UND nicht plan-gebunden."""
    first = delegate.bind_idempotency_key("plan-a", "key-1")
    second = delegate.bind_idempotency_key("plan-b", "key-1")
    assert first != second
    assert len(first) == 64


def test_ohne_control_plane_wird_nicht_gesendet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch(monkeypatch)
    app = FastAPI()
    app.include_router(lc.router)
    client = TestClient(app)
    response = client.post(
        URL,
        json={
            "action": "pay_invoice",
            "params": {"payment_request": DESTINATION},
            "confirm": {"hotp": "123456", "plan_hash": "x", "idempotency_key": "k"},
        },
    )
    assert response.status_code in (403, 503)
