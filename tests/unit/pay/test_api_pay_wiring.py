"""Verdrahtung, Auth und Startguard der Produktschicht (D-CORE-006).

Drei Fragen, die nur die ECHTE App beantwortet:

1. **Faellt ``/pay/*`` in einen Local-Bypass?** ``app/security/auth.py`` laesst
   ``/dashboard/*`` und ``/metrics`` von 127.0.0.1 ohne Auth durch. Die
   Produktschicht darf da nicht hineinrutschen — und zwar nachweislich, nicht
   weil ihr Praefix zufaellig anders lautet.
2. **Bleibt sie ohne Flag wirklich aus?** ``pay_service`` muss ``None`` sein
   und der Poller darf nicht laufen; sonst waere "Default aus" eine Behauptung.
3. **Faengt der Startguard den Zweck ab, den die Policy nicht kennt?** Sonst
   scheitert jede Forderung erst NACH ihrem Journal-Record.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import main as api_main
from app.core.errors import ConfigurationError
from app.core.pay_settings import PaySettings, validate_pay_boot
from app.core.payment_settings import PaymentSettings, get_payment_settings
from app.core.settings import AppSettings
from app.pay.service import PayService

API_KEY = "pay-auth-test-key-not-a-secret"


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #


@pytest.fixture
def guarded_client() -> TestClient:
    from app.api.routers import pay as pay_router
    from app.security.auth import _reset_rate_limit_registry_for_tests, setup_auth

    _reset_rate_limit_registry_for_tests()
    pay_router._reset_rate_limiter_for_tests()
    app = FastAPI()
    app.include_router(pay_router.router)
    setup_auth(app, API_KEY, env="production")
    return TestClient(app)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/pay/requests"),
        ("get", "/pay/requests"),
        ("get", "/pay/requests/pay_1"),
        ("get", "/pay/requests/pay_1/receipt"),
        ("get", "/pay/health"),
    ],
)
def test_kein_pay_pfad_ist_lokal_offen(guarded_client: TestClient, method: str, path: str) -> None:
    """Auch von 127.0.0.1: ohne Bearer 401 — auf JEDEM Pfad des Praefixes."""
    kwargs: dict[str, Any] = {"json": {}} if method == "post" else {}
    response = getattr(guarded_client, method)(path, **kwargs)
    assert response.status_code == 401, f"{method.upper()} {path} war ohne Auth erreichbar"


def test_ein_falscher_bearer_ist_403_nicht_200(guarded_client: TestClient) -> None:
    response = guarded_client.get("/pay/health", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 403


def test_mit_gueltigem_bearer_erreicht_der_aufruf_den_router(guarded_client: TestClient) -> None:
    """404 statt 401: die Auth war erfolgreich, die Schicht ist nur aus."""
    response = guarded_client.get("/pay/health", headers={"Authorization": f"Bearer {API_KEY}"})
    assert response.status_code == 404
    assert response.json()["detail"] == "kai pay disabled"


def test_die_produktschicht_steht_in_keiner_bypass_liste() -> None:
    from app.security.auth import _requires_strong_auth

    source = Path(__import__("app.security.auth", fromlist=["x"]).__file__ or "").read_text(
        encoding="utf-8"
    )
    for marker in ('path in ("", "/health"', 'path == "/dashboard"', 'path == "/metrics"'):
        line = next((ln for ln in source.splitlines() if marker in ln), "")
        assert "/pay" not in line
    assert _requires_strong_auth("/pay/requests") is False


# --------------------------------------------------------------------------- #
# Startguard
# --------------------------------------------------------------------------- #


def test_aus_prueft_gar_nichts() -> None:
    """Ein abgeschalteter Dienst darf den Start nicht an seiner Config aufhalten."""
    validate_pay_boot(
        PaySettings(enabled=False, purpose="unknown"),
        payments=PaymentSettings(purposes_allowed="self_test"),
    )


def test_ein_unbekannter_zweck_verhindert_den_start() -> None:
    with pytest.raises(ConfigurationError, match="APP_PAYMENT_PURPOSES_ALLOWED"):
        validate_pay_boot(
            PaySettings(enabled=True, purpose="kai_pay"),
            payments=PaymentSettings(purposes_allowed="self_test"),
        )


def test_ein_erlaubter_zweck_laesst_den_start_durch() -> None:
    validate_pay_boot(
        PaySettings(enabled=True, purpose="kai_pay"),
        payments=PaymentSettings(purposes_allowed="self_test,kai_pay"),
    )


def test_der_strompfad_loest_relativ_zur_repo_wurzel_auf() -> None:
    """Nicht zum CWD: Server und Werkzeuge starten aus verschiedenen Verzeichnissen."""
    from app.core.payment_settings import REPO_ROOT

    relative = PaySettings(store_path="artifacts/pay/requests.jsonl")
    assert relative.resolved_store_path() == REPO_ROOT / "artifacts" / "pay" / "requests.jsonl"
    absolute = PaySettings(store_path=str(REPO_ROOT / "elsewhere.jsonl"))
    assert absolute.resolved_store_path() == REPO_ROOT / "elsewhere.jsonl"


def test_die_suite_schreibt_nie_in_das_echte_artefakt() -> None:
    """Der Redirect aus ``tests/conftest.py`` greift — sonst waere er eine Zusage ohne Wirkung."""
    from app.core.payment_settings import REPO_ROOT

    assert PaySettings().resolved_store_path() != REPO_ROOT / "artifacts" / "pay" / "requests.jsonl"


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


def _boot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv(
        "APP_PAYMENT_JOURNAL_PATH", str(tmp_path / "payments" / "payment_journal.jsonl")
    )
    monkeypatch.setenv("APP_PAY_STORE_PATH", str(tmp_path / "pay" / "requests.jsonl"))
    get_payment_settings.cache_clear()
    settings = AppSettings(_env_file=None)
    _harness(monkeypatch, settings)
    monkeypatch.setattr(api_main, "REPO_ROOT", tmp_path)
    return api_main.create_app()


def test_ohne_flag_bleibt_die_schicht_aus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_PAY_ENABLED", raising=False)
    app = _boot(tmp_path, monkeypatch)
    with TestClient(app) as client:
        assert app.state.pay_service is None
        assert app.state.pay_poller_task is None
        assert client.get("/pay/health").status_code == 404
    get_payment_settings.cache_clear()


def test_mit_flag_laeuft_dienst_und_poller(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_PAY_ENABLED", "true")
    monkeypatch.setenv("APP_PAYMENT_PURPOSES_ALLOWED", "self_test,kai_pay")
    app = _boot(tmp_path, monkeypatch)
    with TestClient(app) as client:
        assert isinstance(app.state.pay_service, PayService)
        assert app.state.pay_poller_task is not None
        health = client.get("/pay/health").json()
        assert health["enabled"] is True
        assert health["poller_alive"] is True
    # Und nach dem Herunterfahren ist der Task beendet — nicht nur abgebrochen.
    assert app.state.pay_poller_task.done()
    get_payment_settings.cache_clear()


def test_ein_unbekannter_zweck_laesst_den_server_nicht_hochkommen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_PAY_ENABLED", "true")
    monkeypatch.setenv("APP_PAYMENT_PURPOSES_ALLOWED", "self_test")
    app = _boot(tmp_path, monkeypatch)
    with pytest.raises(ConfigurationError, match="APP_PAYMENT_PURPOSES_ALLOWED"), TestClient(app):
        pass  # pragma: no cover - der Start bricht ab
    get_payment_settings.cache_clear()


def test_der_router_haengt_in_der_app(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = AppSettings(_env_file=None)
    _harness(monkeypatch, settings)
    paths = set(api_main.create_app().openapi()["paths"])
    assert {"/pay/requests", "/pay/requests/{payment_id}", "/pay/health"} <= paths
