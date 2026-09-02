"""KAI CORE v1 — Startup-Wahrheit unter Minimal-Env (Mission §10, Startup Test).

Bisher bootete kein Test den Lifespan mit ECHTEM ``validate_secrets``, ECHTER
``build_session_factory`` und ECHTEN Scheduler-Klassen — ``tests/unit/test_api.py``
ersetzt alle Subsysteme durch Lambdas/Fakes und beweist damit die Verdrahtung,
nicht den Boot. Hier läuft ``create_app()`` + Lifespan real; ersetzt wird nur der
Netzpfad (die echten ``httpx``-Transports werfen; der In-Process-Transport des
``TestClient`` bleibt frei), damit kein Scheduler beim Start fetcht.

Fälle:
(a) Minimal-Env (``APP_ENV=testing``, ``DB_URL`` = SQLite-Datei in tmp, alle
    Feature-Flags off, ``APP_API_KEY`` leer): ``/health`` 200 + ``status=="ok"`` +
    ``runtime_commit``-Feld; ``app.state.session_factory`` ist eine echte
    ``async_sessionmaker``, die gegen die tmp-Datei verbindet; RSS-Scheduler läuft
    im Kontext und ist nach Kontext-Exit gestoppt.
(b) Negativ-Boot ``APP_ENV=production`` ohne ``DB_URL``/``OPENAI_API_KEY``/``APP_API_KEY``:
    fail-closed in ZWEI Schichten — ``create_app()`` selbst scheitert bereits an
    ``setup_auth`` (``APP_API_KEY``), und mit gesetztem API-Key scheitert der
    Lifespan an ``validate_secrets`` mit ``DB_URL`` (+ ``OPENAI_API_KEY``) im Text.
    ``DB_URL`` wird als LEERE Env-Variable gesetzt statt nur entfernt: ein
    entferntes Env kann eine ambient ``.env`` nicht überstimmen (D-184-Hygiene),
    und ``validate_secrets`` behandelt leer und nicht-explizit identisch
    (``app/security/secrets.py:58``).

Befund-Notizen:
- ``/health`` braucht keine Tabellen: der Lifespan legt keine an und fragt die DB
  beim Start nicht ab (Alembic bleibt ungetestet, siehe Test-Landkarte §1.5).
- Der Lifespan schreibt ``artifacts/runtime/runtime_identity.json`` unter
  ``REPO_ROOT`` — hier auf tmp umgebogen (Pfad-Redirect, kein Fake).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api import main as api_main
from app.core.errors import ConfigurationError
from app.core.settings import get_settings

_REPO_ROOT = Path(__file__).resolve().parents[2]
_REPO_IDENTITY_ARTIFACT = _REPO_ROOT / "artifacts" / "runtime" / "runtime_identity.json"


def _blocked_send(*_args: object, **_kwargs: object) -> object:
    raise AssertionError("network call during startup is forbidden")


@pytest.fixture
def minimal_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Minimal-Env für einen realen Boot: SQLite-Datei, alle Flags off, kein Netz."""
    db_file = tmp_path / "kai.db"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("APP_API_KEY", "")
    monkeypatch.setenv("DB_URL", f"sqlite+aiosqlite:///{db_file.as_posix()}")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("APP_PIPELINE_PROVIDER", "")
    monkeypatch.setenv("APP_MONITOR_DIR", str(_REPO_ROOT / "monitor"))
    monkeypatch.setenv("ALERT_TELEGRAM_ENABLED", "false")
    monkeypatch.setenv("ALERT_EMAIL_ENABLED", "false")
    monkeypatch.setenv("OPERATOR_TELEGRAM_POLLING_ENABLED", "false")
    monkeypatch.setenv("OPERATOR_TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("EXECUTION_POSITION_MONITOR_ENABLED", "false")
    monkeypatch.setenv("TRADINGVIEW_WEBHOOK_ENABLED", "false")
    monkeypatch.setenv("TRADINGVIEW_BRIDGE_SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("TECHNICAL_PAPER_SCHEDULER_ENABLED", "false")
    # APP_CHAIN_ENABLED / APP_LN_* setzt bereits die autouse-Fixture ``_ln_money_path_inert``.
    # Nur die Netz-Transports sperren — ``TestClient`` spricht über seinen eigenen
    # ASGI-Transport und darf weiter ``/health`` erreichen.
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _blocked_send)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _blocked_send)
    # Runtime-Identity-Artefakt nicht ins Repo schreiben (Pfad-Redirect, kein Fake).
    monkeypatch.setattr(api_main, "REPO_ROOT", tmp_path)
    get_settings.cache_clear()
    yield db_file
    get_settings.cache_clear()


async def _probe_db(session_factory: async_sessionmaker) -> int:
    async with session_factory() as session:
        value = (await session.execute(text("SELECT 1"))).scalar_one()
    await session_factory.kw["bind"].dispose()
    return int(value)


def _repo_identity_fingerprint() -> tuple[bool, int, int]:
    try:
        st = _REPO_IDENTITY_ARTIFACT.stat()
    except OSError:
        return (False, 0, 0)
    return (True, st.st_size, st.st_mtime_ns)


def test_minimal_env_real_boot_health_ok_and_schedulers_stop(minimal_env: Path) -> None:
    db_file = minimal_env
    repo_identity_before = _repo_identity_fingerprint()
    settings = get_settings()
    assert settings.env == "testing"
    assert settings.db.url.endswith("kai.db")
    assert "url" in settings.db.model_fields_set  # explizit gesetzt, kein Default

    app = api_main.create_app()
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["version"] == "0.1.0"
        assert "runtime_commit" in body

        session_factory = app.state.session_factory
        assert isinstance(session_factory, async_sessionmaker)
        assert session_factory.kw["bind"].url.database == db_file.as_posix()

        rss = app.state.rss_scheduler
        assert type(rss).__name__ == "RSSScheduler"
        assert rss._scheduler.running is True
        assert rss._keyword_engine is not None
        assert rss._provider is None  # APP_PIPELINE_PROVIDER leer → kein LLM-Provider
        assert app.state.position_monitor_scheduler is None
        assert app.state.tv_bridge_scheduler is None
        assert app.state.chain_fee_shadow_scheduler is None
        assert app.state.ln_reputation_scheduler is None
        assert app.state.technical_paper_scheduler is None
        assert app.state.telegram_bot.is_configured is False

    # Nach Kontext-Exit: Scheduler gestoppt, Lag-Sampler-Task beendet.
    assert rss._scheduler.running is False
    assert app.state.event_loop_lag_task.done()

    # Die Session-Factory ist echt: sie verbindet gegen die tmp-SQLite-Datei.
    assert asyncio.run(_probe_db(session_factory)) == 1
    assert db_file.exists()

    # Runtime-Identity wurde geschrieben — in tmp, nicht ins Repo.
    identity_artifact = minimal_env.parent / "artifacts" / "runtime" / "runtime_identity.json"
    assert identity_artifact.exists()
    assert _repo_identity_fingerprint() == repo_identity_before


def test_production_boot_without_secrets_fails_closed(
    minimal_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DB_URL", "")  # siehe Modul-Doc (b): leer == nicht explizit gesetzt
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("APP_API_KEY", "")
    get_settings.cache_clear()

    # Schicht 1: ohne APP_API_KEY scheitert bereits ``create_app()`` (setup_auth).
    with pytest.raises(ConfigurationError, match="APP_API_KEY"):
        api_main.create_app()

    # Schicht 2: mit API-Key baut die App, aber der Lifespan verweigert den Boot
    # (validate_secrets: DB_URL nicht explizit + OPENAI_API_KEY leer).
    monkeypatch.setenv("APP_API_KEY", "prod-boot-test-key-not-a-secret")
    get_settings.cache_clear()
    app = api_main.create_app()
    with pytest.raises(ConfigurationError, match="DB_URL") as excinfo:
        with TestClient(app):
            pass
    message = str(excinfo.value)
    assert "APP_ENV=production" in message
    assert "DB_URL is not set explicitly" in message
    assert "OPENAI_API_KEY" in message
    assert "prod-boot-test-key-not-a-secret" not in message
    # Fail-closed: kein Subsystem wurde vor dem Abbruch gestartet.
    assert not hasattr(app.state, "session_factory")
    assert not hasattr(app.state, "rss_scheduler")
