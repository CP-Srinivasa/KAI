"""``/health/payment`` und ``/health/config`` an der HTTP-Grenze (ADR 0018 §10).

Drei Zusagen, die ein Modultest des Schnappschusses nicht abdeckt:

1. **Auth-gated.** Der Endpunkt zeigt Modus, Node-Zustand und Kennzahlen ueber
   Wertbewegungen. Er darf nicht in derselben Liste stehen wie ``/health``.
2. **Ohne verdrahteten Control Plane kein ``ok``.** Eine Antwort, die
   Gesundheit behauptet, ohne etwas gemessen zu haben, ist die eine Sorte
   Health-Endpunkt, die schadet.
3. **``/health/config`` zeigt die Payment-Sektion.** Sie haengt bewusst nicht
   in ``AppSettings`` (God-File-Ratchet) — ohne den Zusatz waere der Geldpfad
   die einzige Konfiguration, die der Operator nicht nachweisen kann.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import health as health_router
from app.core.payment_settings import PaymentSettings, get_payment_settings
from app.payments.journal import PaymentJournal
from app.payments.rails.simulation import SimulationRail
from app.payments.service import PaymentService

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


@pytest.fixture
def app_with_payments(tmp_path: Path) -> FastAPI:
    app = FastAPI()
    app.include_router(health_router.router)
    journal = PaymentJournal(tmp_path / "payments" / "payment_journal.jsonl")
    journal.open()
    rail = SimulationRail(now=NOW)
    app.state.payment_service = PaymentService(
        journal=journal,
        rails={"simulation": rail, "lightning": rail},
        settings=PaymentSettings(mode="simulation"),
        clock=lambda: NOW,
    )
    return app


def test_payment_health_meldet_ok_und_genesis(app_with_payments: FastAPI) -> None:
    client = TestClient(app_with_payments)
    response = client.get("/health/payment")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["mode"] == "simulation"
    assert body["journal"]["chain"] == "ok"
    assert body["journal"]["seq"] == 0
    assert body["rail"]["state"] == "simulated"
    assert response.headers["Cache-Control"].startswith("no-store")


def test_ohne_control_plane_kein_gruen(tmp_path: Path) -> None:
    app = FastAPI()
    app.include_router(health_router.router)
    client = TestClient(app)

    body = client.get("/health/payment").json()

    assert body["status"] == "degraded"
    assert "not wired" in body["reason"]


def test_der_endpunkt_ist_nicht_oeffentlich() -> None:
    """S-001: die oeffentliche Liste in ``auth.py`` nennt ihn NICHT."""
    from app.security import auth

    source = Path(auth.__file__).read_text(encoding="utf-8")
    public_line = [line for line in source.splitlines() if 'if path in ("", "/health"' in line]
    assert public_line, "die oeffentliche Pfadliste hat sich verschoben — Test anpassen"
    assert "/health/payment" not in public_line[0]


def test_health_config_zeigt_die_payment_sektion(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config_redaction import redacted_config_snapshot
    from app.core.settings import AppSettings

    snapshot = redacted_config_snapshot(
        AppSettings(_env_file=None),
        extra_sections={"payments": PaymentSettings(mode="simulation")},
    )

    assert "payments" in snapshot["sections"]
    assert snapshot["sections"]["payments"]["mode"] == "simulation"
    assert snapshot["sections"]["payments"]["daily_hard_cap_sat"] == 25_000


def test_die_payment_sektion_verraet_keine_allowlist_geheimnisse() -> None:
    """Die Allowlist besteht aus Hashes — sie ist kein Geheimnis, aber der
    Schnappschuss darf auch keine Rohziele zeigen."""
    from app.core.config_redaction import redacted_config_snapshot
    from app.core.settings import AppSettings

    settings = PaymentSettings(mode="simulation", destination_allowlist="a" * 64)
    snapshot = redacted_config_snapshot(
        AppSettings(_env_file=None), extra_sections={"payments": settings}
    )

    assert snapshot["sections"]["payments"]["destination_allowlist"] == "a" * 64
    assert "lnbc" not in str(snapshot["sections"]["payments"])


def test_der_router_haengt_die_payment_sektion_selbst_an(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nicht nur die Funktion kann es — der Endpunkt tut es auch."""
    captured: dict[str, Any] = {}

    def _spy(settings: Any, *, extra_sections: Any = None) -> dict[str, Any]:
        captured["extra"] = extra_sections
        return {"sections": {}, "explicit": {}}

    monkeypatch.setattr(health_router, "redacted_config_snapshot", _spy)
    app = FastAPI()
    app.include_router(health_router.router)
    get_payment_settings.cache_clear()

    TestClient(app).get("/health/config")

    assert set(captured["extra"]) == {"payments"}
    assert isinstance(captured["extra"]["payments"], PaymentSettings)
