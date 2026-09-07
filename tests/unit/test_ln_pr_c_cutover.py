"""W0/PR-C — Konsumenten-Cutover: Scopes, Boot-Gate, EIN Journal.

Jeder Test hier gehoert zu genau einem Review-Befund und formuliert die
INVARIANTE, nicht die Implementierung:

  * **C-1**  eine eingeschaltete Capability ohne Credential bootet NICHT (statt
    pro Anfrage still 503 zu liefern);
  * **M-9/BL-2** der oeffentliche Mint ueberlebt ein kaputtes Geldjournal, nimmt
    dessen Lock nie und wird nie 503;
  * **M-11** Einnahmen-Buchung degradiert laut statt still auf 0.

**Was mit ADR 0018 §12 (PR 1) aus dieser Datei verschwunden ist — und wohin.**
Die Haelfte der Befunde beschrieb die ZWEITE Geldkette, die ``ln_control``
neben dem Payment Control Plane fuehrte. Sie ist nicht abgeschaltet, sondern
geloescht; damit sind ihre Waechter gegenstandslos geworden, nicht gelockert:

  * **BL-4** (Freshness-Gate) und **m-13** (Reihenfolge der Verdikte) hingen an
    ``ln_control``s eigener Policy. Der Control Plane hat seine eigene Kette
    (``tests/unit/payments/test_policy.py``), inklusive des Reserve-Bodens.
  * **M-8** (``ACTION_RISK_CLASSES`` als einzige Taxonomie) hatte genau eine
    Aufgabe: zwei Register davon abzuhalten, auseinanderzulaufen. Es gibt nur
    noch eins (``ln_control.ACTIONS``, zwei Eintraege).
  * **Der Spend-Deny bei kaputtem Geldjournal** ist im Control Plane die
    fail-closed ``PaymentJournal.open()`` (``tests/unit/payments/test_journal.py``).
    Die ASYMMETRIE — Spend faellt aus, Mint laeuft — bleibt hier gemessen, aber
    nur noch an ihrer ueberlebenden Haelfte.
  * **MI-2/m-18** (Lese-Redaktion, ein Event = eine Zeile) beschrieb
    ``GET /dashboard/api/ln/ops``. Der Endpunkt ist entfallen; die Redaktion
    selbst lebt in ``app/lightning/receive_ledger.py`` und wird unten geprueft.
  * **m-14** (``error`` = Ausgang unbekannt) hing an ``spent_today_sat_v2``;
    der Tages-Cap kommt aus ``journal_index.totals_for_day``.
"""

from __future__ import annotations

import ast
import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import truth_oracle
from app.core.lightning_settings import (
    LightningBootError,
    LightningSettings,
    validate_lightning_boot,
)
from app.lightning import receive_gate as rg
from app.lightning import receive_ledger
from app.lightning.earnings_booking import EarningsBookingError, book_oracle_earnings

_NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# C-1 — eine eingeschaltete Capability ohne Credential bootet nicht.
# --------------------------------------------------------------------------- #


def _cert(tmp_path: Path) -> str:
    """Gültiges self-signed PEM (wie lnd tls.cert), damit C-1 und nicht TLS greift."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    path = tmp_path / "tls.cert"
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return str(path)


def _boot_cfg(tmp_path: Path, **overrides: Any) -> LightningSettings:
    # _env_file=None + explizite Leerwerte: auf dem Pi liegt eine scharfe .env mit
    # gesetzten Macaroon-Pfaden; ohne diese Isolation prüfte der Test dort NICHTS.
    base: dict[str, Any] = {
        "_env_file": None,
        "enabled": True,
        "tls_cert_path": _cert(tmp_path),
        "macaroon_hex": "read",
        "invoice_macaroon_hex": "",
        "invoice_macaroon_path": "",
        "payment_macaroon_hex": "",
        "payment_macaroon_path": "",
    }
    base.update(overrides)
    return LightningSettings(**base)


@pytest.mark.parametrize("flag", ["l402_enabled", "receive_enabled"])
def test_c1_receive_without_invoice_credential_aborts_boot(tmp_path: Path, flag: str) -> None:
    """Der Bestands-Pi-Fall: EIN Macaroon, Empfang an → heute 503 pro anonymer
    Anfrage (einziger Einnahmepfad still tot). Ab jetzt: lauter Abbruch beim Start."""
    cfg = _boot_cfg(tmp_path, **{flag: True})
    with pytest.raises(LightningBootError, match="invoice credential"):
        validate_lightning_boot(cfg)


def test_c1_pay_enabled_without_payment_credential_aborts_boot(tmp_path: Path) -> None:
    cfg = _boot_cfg(tmp_path, pay_enabled=True)
    with pytest.raises(LightningBootError, match="payment credential"):
        validate_lightning_boot(cfg)


def test_c1_typo_credential_path_aborts_boot(tmp_path: Path) -> None:
    """Ein gesetzter, aber unlesbarer Pfad ist zur Laufzeit nicht von 'fehlt'
    unterscheidbar — beide enden als 503 tief im Request. Hier: eine Zeile beim Start."""
    cfg = _boot_cfg(
        tmp_path, receive_enabled=True, invoice_macaroon_path=str(tmp_path / "nope.macaroon")
    )
    with pytest.raises(LightningBootError, match="unreadable"):
        validate_lightning_boot(cfg)


def test_c1_provisioned_capabilities_boot(tmp_path: Path) -> None:
    cfg = _boot_cfg(
        tmp_path,
        l402_enabled=True,
        receive_enabled=True,
        pay_enabled=True,
        invoice_macaroon_hex="invoice",
        payment_macaroon_hex="payment",
    )
    validate_lightning_boot(cfg)  # must not raise


def test_c1_does_not_demand_credentials_for_switched_off_capabilities(tmp_path: Path) -> None:
    """Nur EINGESCHALTETE Capabilities sind boot-blockierend: onchain/channel haben
    keinen Schalter und melden sich interaktiv im Cockpit, nicht als stilles Loch."""
    validate_lightning_boot(_boot_cfg(tmp_path))  # read-only Betrieb bleibt startfähig


# --------------------------------------------------------------------------- #
# M-9 / BL-2 — asymmetrisches Fail-Verhalten: der Mint ueberlebt.
# --------------------------------------------------------------------------- #


def _money_journal_path() -> Path:
    """Das Geld-Journal, das es HEUTE gibt (ADR 0018 §5).

    Bis PR 2 zeigte diese Hilfe auf das alte v2-Journal. Nach dessen Rueckbau
    waere das eine Datei, die kein Produktionsmodul mehr liest — und ein
    Waechter, der eine unbeachtete Datei zerstoert, beweist nichts. Die
    BL-2-Asymmetrie gilt weiter, sie wird nur am lebenden Buch gemessen.
    """
    from app.core.payment_settings import get_payment_settings

    return get_payment_settings().resolved_journal_path()


def _break_money_journal() -> Path:
    """Ein unverketteter Row + abgerissener Tail = maximal kaputtes Journal."""
    path = _money_journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"ts": "2026-08-01T00:00:00+00:00", "action": "keysend", "state": "executed"})
        + '\n{"ts": "2026-08-0',
        encoding="utf-8",
    )
    return path


def _ln_cfg(**kw: Any) -> LightningSettings:
    return LightningSettings(
        _env_file=None,
        enabled=True,
        tls_cert_path="test-tls.pem",
        macaroon_hex="read",
        invoice_macaroon_hex="invoice",
        payment_macaroon_hex="payment",
        onchain_macaroon_hex="onchain",
        channel_macaroon_hex="channel",
        **kw,
    )


async def test_receive_mints_even_with_a_broken_money_journal_and_a_dead_audit(
    tmp_path, monkeypatch
) -> None:
    """GEFORDERTER BEWEIS 2 (BL-2): der Mint ueberlebt BEIDE Journale.

    Kaputtes Alt-Geldjournal + nicht beschreibbares Receive-Journal ⇒ die
    Rechnung wird trotzdem erzeugt. Ein anonymer Zahler darf nie an unserem
    Audit scheitern.
    """
    _break_money_journal()
    blocked = tmp_path / "not-a-dir"
    blocked.write_text("occupied", encoding="utf-8")
    monkeypatch.setenv("APP_LN_RECEIVE_LEDGER_PATH", str(blocked / "receive.jsonl"))

    client = MagicMock()
    client.add_invoice = AsyncMock(return_value={"payment_request": "lnbc1", "r_hash": "aa"})
    with patch("app.lightning.receive_gate._build_client", return_value=client):
        result = await rg.create_invoice(
            value_sat=10, memo="kai-oracle:x", dry_run=False, cfg=_ln_cfg(receive_enabled=True)
        )
    assert result.state == "executed"
    client.add_invoice.assert_awaited_once()


async def test_m9_mint_never_takes_the_money_journal_lock_or_rescans_it(monkeypatch) -> None:
    """M-9: kein Exklusiv-Lock, kein O(n)-Full-Rescan im oeffentlichen Mint-Hotpath.

    Gemessen waren 2000 Mints ≈ 95 s kumulativ mit O(n²)-Wachstum, von aussen
    treibbar. Strukturell statt per Benchmark bewiesen: die verbliebene
    Journal-Maschinerie und portalocker selbst explodieren hier — der Mint
    laeuft trotzdem durch.

    Der Waechter zeigt seit PR 2 auf das Geld-Journal, das es noch GIBT: das
    alte v2-Journal ist Archiv ohne Modul, ein Patch darauf waere ein Patch auf
    nichts. Gepatcht wird deshalb der Interprozess-Lock selbst (``portalocker``,
    prozessweit) und der Schreibpfad des Payment-Journals. Der Waechter faellt
    in dem Moment, in dem jemand die Kopplung wieder einzieht.
    """
    import portalocker

    from app.payments.journal import PaymentJournal

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("mint path must not touch the money journal")

    monkeypatch.setattr(portalocker, "Lock", _boom)
    monkeypatch.setattr(PaymentJournal, "open", _boom)
    monkeypatch.setattr(PaymentJournal, "append", _boom)
    _break_money_journal()

    client = MagicMock()
    client.add_invoice = AsyncMock(return_value={"payment_request": "lnbc1", "r_hash": "aa"})
    with patch("app.lightning.receive_gate._build_client", return_value=client):
        result = await rg.create_invoice(
            value_sat=10, dry_run=False, cfg=_ln_cfg(receive_enabled=True)
        )
    assert result.state == "executed"
    assert len(receive_ledger.read_recent_receive_events()) == 1  # genau EINE Audit-Zeile


def test_the_mint_path_does_not_even_know_the_money_journal() -> None:
    """Strukturell, nicht per Patch: ``receive_gate`` kennt kein Geld-Journal.

    Bis PR 2 hiess die verbotene Kante ``app.lightning.ops_ledger``. Mit dem
    Wegfall des Moduls waere diese eine Zusage trivial wahr geworden — eine
    Wache, die nichts mehr ausschliessen kann. Verboten ist deshalb ab jetzt
    der Sendepfad, den es GIBT: der anonyme Mint darf nie hinter den
    exklusiven Geldjournal-Lock geraten (BL-2/M-9).
    """
    source = (Path("app/lightning/receive_gate.py")).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    forbidden = {
        "app.lightning.ops_ledger",
        "app.payments.journal",
        "app.payments.service",
        "portalocker",
    }
    assert not (imported & forbidden), sorted(imported & forbidden)


def test_receive_journal_is_redacted_and_separate() -> None:
    receive_ledger.append_receive_event(
        "create_invoice",
        "executed",
        plan={"value_sat": 10, "memo": "kai-oracle:secret-scope"},
        response={
            "payment_request": "lnbc10n1raw-invoice",
            "r_hash": base64.b64encode(b"\x01" * 32).decode(),
        },
    )
    text = receive_ledger.receive_ledger_path().read_text(encoding="utf-8")
    assert "lnbc10n1raw-invoice" not in text and "secret-scope" not in text
    row = json.loads(text.splitlines()[0])
    assert row["action"] == "create_invoice" and row["plan"]["value_sat"] == 10
    assert row["response"]["payment_hash"] == (b"\x01" * 32).hex()
    assert not _money_journal_path().exists()  # das Geld-Journal bleibt unberuehrt


def test_public_oracle_mint_returns_402_not_503_with_a_broken_money_journal(
    tmp_path, monkeypatch
) -> None:
    """BL-2 end-to-end ueber den ECHTEN /oracle-Pfad: kaputtes Alt-Geldjournal,
    echter Empfangs-Gate-Aufruf (nur der lnd-Client ist gefaelscht) ⇒ 402 mit
    Challenge. Vorher: 503 pro anonymer Anfrage + zwei Journal-Zeilen."""
    _break_money_journal()
    monkeypatch.setenv("APP_LN_ENABLED", "true")
    monkeypatch.setenv("APP_LN_RECEIVE_ENABLED", "true")
    monkeypatch.setenv("APP_LN_TLS_CERT_PATH", "test-tls.pem")
    monkeypatch.setenv("APP_LN_INVOICE_MACAROON_HEX", "invoice")
    monkeypatch.setenv("APP_LN_L402_ENABLED", "true")
    monkeypatch.setenv("APP_LN_L402_SECRET", "oracle-secret")
    from app.core.settings import get_settings

    get_settings.cache_clear()

    truth_oracle.reset_mint_limiter()
    app = FastAPI()
    app.include_router(truth_oracle.router)
    node = MagicMock()
    node.add_invoice = AsyncMock(
        return_value={
            "payment_request": "lnbc10n1challenge",
            "r_hash": base64.b64encode(hashlib.sha256(b"x").digest()).decode(),
        }
    )
    with patch("app.lightning.receive_gate._build_client", return_value=node):
        r = TestClient(app, raise_server_exceptions=False).get("/oracle/onchain-facts")
    assert r.status_code == 402, r.text
    assert 'invoice="lnbc10n1challenge"' in r.headers.get("WWW-Authenticate", "")
    node.add_invoice.assert_awaited_once()


# --------------------------------------------------------------------------- #
# ADR 0018 §12 — das Alt-Journal ist Archiv.
# --------------------------------------------------------------------------- #


def test_the_old_money_journal_has_no_module_left() -> None:
    """PR 2: kein Schreiber, kein Leser, kein Modul.

    PR 1 hatte ``ops_ledger`` auf ein Archiv geschrumpft, das einen offenen
    Alt-Vorgang noch schliessen konnte. Diese Faehigkeit hatte genau einen
    Aufrufer, den Crash-Gap-Reconciler; mit ihm ist sie gegenstandslos. Ein
    Modul, das ein Geldjournal schreiben KANN und das niemand ruft, ist keine
    Reserve, sondern ein offener Weg.

    ``artifacts/ln_ops_ledger_v2.jsonl`` bleibt unangetastet am Geraet und im
    Backup-Vertrag — geloescht wird der Code, nicht der Beweis.
    """
    import importlib.util

    assert importlib.util.find_spec("app.lightning.ops_ledger") is None
    assert importlib.util.find_spec("app.lightning.reconciliation") is None
    assert importlib.util.find_spec("app.payments.reconcile_dual") is None


def test_no_production_module_names_the_old_money_journal_module() -> None:
    """Kein Import, auch kein verzoegerter, auf das verschwundene Archivmodul.

    Der gefaehrliche Rest waere ein Funktionsrumpf-Import (das Muster, mit dem
    ``reconcile_dual`` den Paketzyklus umging). Ein Importgraph auf Modulebene
    sieht den nicht — ``ast.walk`` sieht ihn, egal in welcher Tiefe er steht.
    Prosa-Erwaehnungen in Docstrings sind ausdruecklich erlaubt: sie erklaeren,
    warum etwas weg ist.
    """
    gone = {"app.lightning.ops_ledger", "app.lightning.reconciliation"}
    offenders: list[str] = []
    for path in sorted(Path("app").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in gone:
                offenders.append(f"{path}:{node.lineno} from {node.module}")
            elif isinstance(node, ast.Import):
                offenders += [
                    f"{path}:{node.lineno} import {a.name}" for a in node.names if a.name in gone
                ]
    assert not offenders, offenders


# --------------------------------------------------------------------------- #
# M-11 — laute Degradation statt stiller 0.
# --------------------------------------------------------------------------- #


async def test_m11_booking_raises_instead_of_pretending_zero() -> None:
    """Ein gruener Timer mit '0 gebucht' ist von 'niemand hat gezahlt' nicht zu
    unterscheiden — fuer eine Wahrheits-Plattform die schlechteste Fehlerart."""
    from app.lightning.client import LightningUnavailableError

    failing = MagicMock()
    failing.list_invoices = AsyncMock(side_effect=LightningUnavailableError("node down"))
    with patch("app.lightning.earnings_booking._build_client", return_value=failing):
        with pytest.raises(EarningsBookingError, match="UNKNOWN"):
            await book_oracle_earnings(cfg=_ln_cfg())


async def test_m11_missing_invoice_credential_is_loud_too() -> None:
    """Genau der C-1-Zustand: ohne Invoice-Credential buchte der Job still 0 — die
    Treasury-Zahl waere dauerhaft falsch, ohne dass irgendetwas rot wird."""
    cfg = LightningSettings(
        _env_file=None, enabled=True, tls_cert_path="test-tls.pem", macaroon_hex="read"
    )
    with pytest.raises(EarningsBookingError):
        await book_oracle_earnings(cfg=cfg)


async def test_m11_disabled_lightning_is_still_an_honest_zero() -> None:
    assert await book_oracle_earnings(cfg=LightningSettings(_env_file=None, enabled=False)) == 0


def test_m11_timer_script_exits_nonzero_on_a_degraded_run() -> None:
    """Der Betriebs-Beweis: die systemd-Unit wird ROT. Ein gruener Timer ueber einer
    nicht aktualisierten Treasury ist genau die stille Falschaussage aus M-11."""
    import asyncio

    from scripts.book_oracle_earnings import _main

    with patch(
        "scripts.book_oracle_earnings.book_all_earnings",
        AsyncMock(side_effect=EarningsBookingError("node down")),
    ):
        assert asyncio.run(_main()) == 1
    with patch(
        "scripts.book_oracle_earnings.book_all_earnings",
        AsyncMock(return_value={"oracle-l402": 0, "lnurlp": 0}),
    ):
        assert asyncio.run(_main()) == 0  # ehrliche Null bleibt gruen
