"""Verdrahtung und Auth des Geldpfads (ADR 0017 §10/§11).

Zwei Fragen, die nur die ECHTE App beantwortet:

1. **Faellt ``/payments/*`` in einen Local-Bypass?** ``app/security/auth.py``
   laesst ``/dashboard/*`` und ``/metrics`` von 127.0.0.1 ohne Auth durch
   (F-002). Der Geldpfad darf da nicht hineinrutschen — und zwar nicht, weil
   sein Praefix zufaellig anders lautet, sondern nachweislich.
2. **Baut der Lifespan den Control Plane und klaert er offene Sends?** Ein
   ``submitted`` ohne Antwort im Journal heisst: der Vorgaenger ist
   abgestuerzt, waehrend Geld unterwegs war. Wer das erst beim naechsten
   Zugriff klaert, hat einen Intent, den nie wieder jemand anfasst.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import main as api_main
from app.core.payment_settings import PaymentSettings, get_payment_settings
from app.core.settings import AppSettings
from app.payments.enums import PaymentStatus
from app.payments.journal import PaymentJournal
from app.payments.service import PaymentService

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
API_KEY = "payments-auth-test-key-not-a-secret"


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #


@pytest.fixture
def guarded_client() -> TestClient:
    from app.api.routers import payments as payments_router
    from app.security.auth import _reset_rate_limit_registry_for_tests, setup_auth

    _reset_rate_limit_registry_for_tests()
    app = FastAPI()
    app.include_router(payments_router.router)
    setup_auth(app, API_KEY, env="production")
    return TestClient(app)


@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/payments/intents"),
        ("get", "/payments/intents/pi_1"),
        ("post", "/payments/intents/pi_1/simulate"),
        ("post", "/payments/intents/pi_1/execute"),
        ("post", "/payments/invoices"),
        ("get", "/payments/invoices/abc"),
        ("get", "/payments/audit"),
    ],
)
def test_kein_payment_pfad_ist_lokal_offen(
    guarded_client: TestClient, method: str, path: str
) -> None:
    """Auch von 127.0.0.1: ohne Bearer 401 — auf JEDEM Pfad des Praefixes."""
    kwargs = {"json": {}} if method == "post" else {}
    response = getattr(guarded_client, method)(path, **kwargs)
    assert response.status_code == 401, f"{method.upper()} {path} war ohne Auth erreichbar"


def test_ein_falscher_bearer_ist_403_nicht_200(guarded_client: TestClient) -> None:
    response = guarded_client.get("/payments/audit", headers={"Authorization": "Bearer wrong-key"})
    assert response.status_code == 403


def test_mit_gueltigem_bearer_erreicht_der_aufruf_den_router(
    guarded_client: TestClient,
) -> None:
    """503 statt 401: die Auth war erfolgreich, der Control Plane fehlt nur."""
    response = guarded_client.get("/payments/audit", headers={"Authorization": f"Bearer {API_KEY}"})
    assert response.status_code == 503


def test_der_geldpfad_steht_in_keiner_bypass_liste() -> None:
    from app.security.auth import _requires_strong_auth

    source = Path(__import__("app.security.auth", fromlist=["x"]).__file__ or "").read_text(
        encoding="utf-8"
    )
    for marker in ('path in ("", "/health"', 'path == "/dashboard"', 'path == "/metrics"'):
        line = next((ln for ln in source.splitlines() if marker in ln), "")
        assert "/payments" not in line
    # Und der Geldpfad braucht keinen Eintrag in ``_requires_strong_auth``: er
    # faellt durch alle Bypass-Zweige und landet bei CF-Access/Bearer.
    assert _requires_strong_auth("/payments/intents") is False


# --------------------------------------------------------------------------- #
# Lifespan
# --------------------------------------------------------------------------- #


def _harness(monkeypatch: pytest.MonkeyPatch, settings: AppSettings) -> None:
    settings.operator.telegram_polling_enabled = False
    settings.operator.telegram_bot_token = ""
    settings.operator.admin_chat_ids = ""
    settings.providers.openai_api_key = ""
    monkeypatch.setattr(api_main, "get_settings", lambda: settings)
    monkeypatch.setattr(api_main, "configure_logging", lambda _level: None)
    monkeypatch.setattr(api_main, "validate_secrets", lambda _s: None)
    monkeypatch.setattr(api_main, "setup_auth", lambda *_a, **_kw: None)
    monkeypatch.setattr(api_main, "build_session_factory", lambda _db: "session-factory")
    monkeypatch.setattr(
        api_main,
        "RSSScheduler",
        type(
            "FakeRSS",
            (),
            {
                "__init__": lambda self, *a, **kw: None,
                "start": lambda self: None,
                "stop": lambda self: None,
            },
        ),
    )
    monkeypatch.setattr(
        api_main,
        "KeywordEngine",
        type("FakeKE", (), {"from_monitor_dir": staticmethod(lambda _p: "fake-ke")}),
    )


def test_der_lifespan_baut_den_control_plane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "APP_PAYMENT_JOURNAL_PATH", str(tmp_path / "payments" / "payment_journal.jsonl")
    )
    get_payment_settings.cache_clear()
    settings = AppSettings(_env_file=None)
    _harness(monkeypatch, settings)
    monkeypatch.setattr(api_main, "REPO_ROOT", tmp_path)

    app = api_main.create_app()
    with TestClient(app):
        service = app.state.payment_service
        assert isinstance(service, PaymentService)
        assert service.settings.mode == "simulation"
        assert service.rail.name == "lightning"
        assert service.journal.path == tmp_path / "payments" / "payment_journal.jsonl"
    get_payment_settings.cache_clear()


def test_der_lifespan_klaert_einen_abgestuerzten_send(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein ``submitted`` ohne Antwort wird RECONCILIATION_REQUIRED, nie FAILED."""
    journal_path = tmp_path / "payments" / "payment_journal.jsonl"
    seed = PaymentJournal(journal_path)
    seed.open()
    seed.append("pi_crash", "intent_created", {"status": "REQUESTED"}, ts=NOW)
    seed.append(
        "pi_crash",
        "submitted",
        {"status": "SUBMITTED", "rail_dedup_key": "a" * 64, "amount_sent_minor_units": 500},
        ts=NOW,
    )

    monkeypatch.setenv("APP_PAYMENT_JOURNAL_PATH", str(journal_path))
    get_payment_settings.cache_clear()
    settings = AppSettings(_env_file=None)
    _harness(monkeypatch, settings)
    monkeypatch.setattr(api_main, "REPO_ROOT", tmp_path)

    app = api_main.create_app()
    with TestClient(app):
        status = app.state.payment_service.journal.index.intent_status("pi_crash")
        assert status == PaymentStatus.RECONCILIATION_REQUIRED.value
    get_payment_settings.cache_clear()


def test_der_router_haengt_in_der_app(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = AppSettings(_env_file=None)
    _harness(monkeypatch, settings)
    app = api_main.create_app()
    paths = {route.path for route in app.routes if hasattr(route, "path")}  # type: ignore[attr-defined]
    assert "/payments/intents" in paths
    assert "/health/payment" in paths


def test_der_simulationsmodus_baut_keinen_lightning_rail() -> None:
    from app.core.lightning_settings import LightningSettings
    from app.payments.wiring import build_rails

    rails: dict[str, Any] = build_rails(PaymentSettings(mode="simulation"), LightningSettings())
    assert set(rails) == {"simulation"}
    assert type(rails["simulation"]).__name__ == "SimulationRail"
