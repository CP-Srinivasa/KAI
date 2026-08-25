from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app


@pytest.fixture(autouse=True)
def _pin_feature_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force feature-flag defaults for the whole suite (D-184 hygiene).

    The repo ships a local ``.env`` for operator convenience (e.g. with
    ``EXECUTION_PAPER_MIN_PRIORITY=10`` to activate the D-182 gate). Tests
    must never depend on that ambient state — they verify code behaviour
    against the documented *default*, not the operator's current toggle.
    Individual tests that want a different state opt in via their own
    ``monkeypatch.setenv`` (function scope wins over earlier setenv calls
    on the same key).
    """
    monkeypatch.setenv("EXECUTION_PAPER_MIN_PRIORITY", "1")
    # P2: pretend tests run on the Pi so the off-Pi `probe_location` warning
    # (app.alerts.health_check) doesn't pollute unrelated assertions. Tests
    # that exercise the off-Pi path opt in via monkeypatch.setenv to a
    # non-matching marker.
    import socket as _socket

    _host = _socket.gethostname() or "test-host"
    monkeypatch.setenv("KAI_PI_HOSTNAME_MARKER", _host.lower())
    # Folge davon: `runs_on_pi` ist in JEDEM Test wahr. Die sudo-Policy-Probe
    # ruft aber einen externen Prozess (`sudo -n -l`), und auf einem CI-Runner
    # mit passwortlosem sudo erzeugte sie prompt einen Befund, der fremde
    # Assertions umwarf (lokal blieb es unbemerkt, weil Windows kein sudo hat).
    # Tests, die die Probe selbst pruefen, entfernen die Variable.
    monkeypatch.setenv("KAI_SUDO_POLICY_PROBE", "off")
    # Gleiche Klasse, gleiche Falle: die Broker-Probe fragt, ob
    # /usr/local/sbin/kai-service-control existiert. Auf JEDER Maschine ausser
    # der Pi existiert es nicht — die Probe meldete daraufhin in jedem Test
    # einen kritischen Befund und warf fremde Assertions um
    # (test_daily_briefing, test_notify). Die Timer-Probe kommt vorsorglich
    # mit: sie ueberlebte bisher nur zufaellig, weil `systemctl` auf der
    # Workstation fehlt und der Aufruf in einen OSError lief.
    monkeypatch.setenv("KAI_BROKER_PROBE", "off")
    monkeypatch.setenv("KAI_TIMER_SCHEDULE_PROBE", "off")
    # STAB-02: die Runtime-Identitaets-Probe meldet ein fehlendes Artefakt auf der
    # "Pi" — in Fixtures ohne laufenden Server waere das jedes Mal ein Befund.
    monkeypatch.setenv("KAI_RUNTIME_IDENTITY_PROBE", "off")


@pytest.fixture(autouse=True)
def _ln_money_path_inert(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Live-Fire-Sperre für die Wert-Schicht (Befund 05.08., W0-Nachtrag).

    Die Unit-Suite lief auf dem Pi gegen die SCHARFE Wert-Schicht: ein
    Endpoint-Test erreichte den echten Node (``send_coins`` — von lnd nur
    wegen ungültiger Adresse abgelehnt), Test-Rows landeten im
    Prod-Ops-Ledger, und ``reset_control_state()`` leerte den persistenten
    Idempotenz-Store. Drei Garantien machen das strukturell unmöglich,
    egal auf welcher Box die Suite läuft:

    1. **Kill-Switch:** die Geld-Gates als OS-Env (schlägt die ``.env``-Datei
       in der pydantic-settings-Präzedenz; Tests mit explizitem
       ``LightningSettings(...)``-Objekt gewinnen weiterhin).
    2. **Ops-Ledger-Redirect:** Writer und ``spent_today_sat`` arbeiten auf
       einem tmp-File, nie auf ``artifacts/ln_ops_ledger.jsonl``. Gilt für
       ALLE DREI Journale — v1 (Pfadkonstante), v2
       (``APP_LN_OPS_LEDGER_V2_PATH``) und seit dem PR-C-Cutover das getrennte
       Receive-Journal (``APP_LN_RECEIVE_LEDGER_PATH``), sonst schriebe die
       Suite in die echten Geld-/Empfangs-Journale.
    3. **Idempotenz-Redirect:** der Cockpit-Singleton zeigt auf einen
       frischen tmp-Store — ``reset_control_state()`` kann keinen
       Prod-Zustand mehr löschen.
    """
    monkeypatch.setenv("APP_LN_ENABLED", "false")
    monkeypatch.setenv("APP_LN_PAY_ENABLED", "false")
    monkeypatch.setenv("APP_LN_RECEIVE_ENABLED", "false")
    # Chain-Truth (read-only bitcoind-Probe, kein Geldpfad) ebenfalls auf
    # Code-Default zwingen: die Pi-.env aktiviert sie, und ein Default-off-Test
    # sähe sonst `pending` statt `disabled` (Rest-Fail vom 05.08.).
    monkeypatch.setenv("APP_CHAIN_ENABLED", "false")

    from app.lightning import ops_ledger

    monkeypatch.setattr(ops_ledger, "_OPS_PATH", tmp_path / "ln_ops_ledger.jsonl")
    monkeypatch.setenv("APP_LN_OPS_LEDGER_V2_PATH", str(tmp_path / "ln_ops_ledger_v2.jsonl"))
    monkeypatch.setenv("APP_LN_RECEIVE_LEDGER_PATH", str(tmp_path / "ln_receive_ledger.jsonl"))

    from app.api.routers import ln_control
    from app.lightning.idempotency_store import PersistentSeenKeys

    monkeypatch.setattr(
        ln_control, "_seen_idempotency", PersistentSeenKeys(tmp_path / "ln_seen_keys.jsonl")
    )


_REPO_PAPER_AUDIT = (
    Path(__file__).resolve().parents[1] / "artifacts" / "paper_execution_audit.jsonl"
)


def _paper_audit_fingerprint() -> tuple[bool, int, int]:
    try:
        st = _REPO_PAPER_AUDIT.stat()
    except OSError:
        return (False, 0, 0)
    return (True, st.st_size, st.st_mtime_ns)


@pytest.fixture(autouse=True)
def _paper_audit_stream_untouched() -> Iterator[None]:
    """Wächter: kein Test darf den Produktions-Evidenz-Stream anfassen (Befund 09.08.).

    ``PaperExecutionEngine`` fällt ohne ``audit_log_path`` auf das relative
    ``artifacts/paper_execution_audit.jsonl`` zurück — die Datei, aus der die
    Verdikte gelesen werden. Auf dem lokalen Stand stehen dort 230 Zeilen mit
    Fixture-Symbolen (TIGHT/WIN/WIDE/FOO/BAR/XYZ/BIRB/USDT); jede Kennzahl
    darüber ist damit kontaminiert, unbestimmt in welche Richtung.

    Bewusst ein Wächter und **kein** Redirect: die saubere Isolation dieser
    Suite läuft über ``monkeypatch.chdir(tmp_path)`` und nutzt genau die
    Relativität dieses Pfads. Ein Patch auf einen absoluten tmp-Pfad hebelt sie
    aus — der Code schriebe dann woanders hin als der Test liest (fünf Tests
    sind daran gescheitert). Der Wächter ändert kein Verhalten; er macht
    fehlende Isolation sichtbar, statt sie stillschweigend zu ersetzen.
    """
    before = _paper_audit_fingerprint()
    yield
    after = _paper_audit_fingerprint()
    if before != after:
        pytest.fail(
            "Dieser Test hat den PRODUKTIONS-Evidenz-Stream verändert "
            f"({_REPO_PAPER_AUDIT}). Aus dieser Datei werden Verdikte gelesen — "
            "sie darf von der Suite nie berührt werden. Isoliere den Test mit "
            "monkeypatch.chdir(tmp_path) oder übergib audit_log_path explizit."
        )


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> None:
    """Clear the get_settings() lru_cache around every test (settings-cache fix).

    get_settings() is now process-cached (@lru_cache). Tests monkeypatch env per
    case (see _pin_feature_defaults + the `client` fixture); without clearing the
    cache the first cached AppSettings would leak across the whole session and
    ignore those per-test env overrides. Clear before AND after each test so each
    case resolves its own environment on the first get_settings() call.
    """
    from app.core.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_paper_engine_singleton() -> None:
    """Drop the PaperExecutionEngine singleton between tests (P1 #7).

    The 2026-05-14 singleton-refactor (``app.execution.paper_engine_singleton``)
    means one engine instance is reused across all consumers within a process.
    In production that is correct; in tests it would leak ``_filled_keys`` and
    portfolio state from one case into the next (Bridge tests open env-001 →
    next test re-fires env-001 → DuplicateOrderError). Clearing the cache
    before and after every test enforces deterministic per-test isolation.
    """
    from app.execution.paper_engine_singleton import reset_paper_engine_cache

    reset_paper_engine_cache()
    yield
    reset_paper_engine_cache()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient with auth disabled (APP_ENV=testing, no API key).

    Auth middleware is tested separately in test_auth.py with isolated
    FastAPI instances.  All other API tests use this fixture and expect
    unauthenticated access to work.
    """
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("APP_API_KEY", "")
    return TestClient(create_app())
