"""W0/PR-C — Konsumenten-Cutover: Scopes, Boot-Gate, EIN Journal, EINE Taxonomie.

Jeder Test hier gehört zu genau einem Review-Befund und formuliert die INVARIANTE,
nicht die Implementierung:

  * **C-1**  eine eingeschaltete Capability ohne Credential bootet NICHT (statt pro
    Anfrage still 503 zu liefern);
  * **BL-4** das Freshness-Gate hängt weiter am kanonischen ``get_capital_grade_status``
    (#638, live verifiziert) — kein toter Accessor, kein Direkt-Poll;
  * **M-8**  ``policy.ACTION_RISK_CLASSES`` ist die einzige Risiko-Taxonomie;
  * **M-9/BL-2** der öffentliche Mint überlebt ein kaputtes Geldjournal, nimmt dessen
    Lock nie und wird nie 503 — der Spend im selben Zustand wird abgelehnt;
  * **M-11** Einnahmen-Buchung degradiert laut statt still auf 0;
  * **MI-2/m-18** der Lesepfad redigiert und zählt ein Geld-Event genau einmal;
  * **m-13/14/15** entschiedene Reihenfolge, EINE Fehlschlag-Semantik, geschlossene
    UTC-Tagesgrenze.
"""

from __future__ import annotations

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

from app.api.routers import ln_control as lc
from app.api.routers import truth_oracle
from app.core.lightning_settings import (
    LightningBootError,
    LightningSettings,
    validate_lightning_boot,
)
from app.lightning import ops_ledger, receive_ledger
from app.lightning import value_layer as vl
from app.lightning.earnings_booking import EarningsBookingError, book_oracle_earnings
from app.lightning.ops_ledger import (
    append_ln_op,
    append_ln_outcome,
    ln_ops_v2_path,
    prepare_ln_intent,
    read_recent_ln_ops,
    spent_today_sat_v2,
)
from app.lightning.policy import (
    ACTION_RISK_CLASSES,
    PolicyEnvelope,
    evaluate_policy,
    is_capital_action,
)
from app.lightning.value_layer import RECEIVE_ACTIONS

_NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
_URL = "/dashboard/api/ln/value-action"


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
# BL-4 — Freshness bleibt am kanonischen Accessor aus #638.
# --------------------------------------------------------------------------- #


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(lc.router)
    return app


async def _bal_million() -> int:
    return 1_000_000


def _envelope(**kw: Any) -> PolicyEnvelope:
    return PolicyEnvelope(**kw)


def _patch_cockpit(monkeypatch: pytest.MonkeyPatch, envelope: PolicyEnvelope) -> None:
    lc.reset_control_state()
    monkeypatch.setattr(lc.PolicyStore, "load", lambda self: envelope)
    monkeypatch.setattr(lc, "_available_balance_sat", _bal_million)
    monkeypatch.setattr(lc, "spent_today_sat_v2", lambda: 0)


def _rich_status() -> Any:
    return MagicMock(wallet_total_sat=1_000_000, channel_local_sat=0, balances_available=True)


def test_bl4_capital_freshness_reads_the_canonical_capital_grade_accessor(monkeypatch) -> None:
    """#638 ist die Quelle — NICHT ein Direkt-Poll. Der Test patcht bewusst
    ``app.lightning.cache.get_capital_grade_status`` (nicht den Router-Helfer): wäre
    der Accessor toter Code, bliebe dieser Test grün ohne Aussage — hier schlägt er um."""
    _patch_cockpit(
        monkeypatch,
        _envelope(
            allowed_actions=frozenset({"send_coins"}),
            per_action_cap_sat=1_000_000,
            daily_cap_sat=1_000_000,
        ),
    )
    calls: list[str] = []

    async def _fresh() -> tuple[Any, float]:
        calls.append("fresh")
        return _rich_status(), 1.0

    async def _stale() -> tuple[Any, float]:
        calls.append("stale")
        return None, 999.0

    params = {"addr": "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4", "amount_sat": 1000}
    with patch("app.lightning.cache.get_capital_grade_status", _fresh):
        ok = TestClient(_app()).post(_URL, json={"action": "send_coins", "params": params})
    with patch("app.lightning.cache.get_capital_grade_status", _stale):
        denied = TestClient(_app()).post(_URL, json={"action": "send_coins", "params": params})

    assert calls == ["fresh", "stale"]  # der kanonische Accessor wird wirklich benutzt
    assert ok.json()["policy"]["decision"] == "auto_execute"
    assert denied.json()["policy"]["decision"] == "denied"
    assert "stale" in denied.json()["policy"]["reason"]


def test_bl4_receive_action_never_consults_the_capital_accessor(monkeypatch) -> None:
    """Das Freshness-Gate bindet Kapital, nicht Empfang — sonst nähme ein degradierter
    Node den einzigen Einnahmepfad mit."""
    _patch_cockpit(monkeypatch, _envelope(allowed_actions=frozenset({"create_invoice"})))
    calls: list[str] = []

    async def _boom() -> tuple[Any, float]:
        calls.append("called")
        return None, 0.0

    with patch("app.lightning.cache.get_capital_grade_status", _boom):
        r = TestClient(_app()).post(
            _URL, json={"action": "create_invoice", "params": {"value_sat": 10}}
        )
    assert calls == []
    assert r.json()["policy"]["decision"] == "auto_execute"


# --------------------------------------------------------------------------- #
# M-8 — eine Taxonomie, beidseitig deckungsgleich.
# --------------------------------------------------------------------------- #


def test_m8_control_surface_and_policy_taxonomy_are_congruent() -> None:
    assert set(lc._ACTIONS) == set(ACTION_RISK_CLASSES), (
        "jede erreichbare Aktion MUSS klassifiziert sein und umgekehrt — sonst "
        "entscheidet wieder eine zweite, unattestierte Kopie über Kapitalwirkung"
    )


def test_m8_capital_semantics_come_only_from_the_policy_taxonomy() -> None:
    assert {a for a in lc._ACTIONS if is_capital_action(a)} == {
        "pay_invoice",
        "keysend",
        "send_coins",
        "open_channel",
        "close_channel",
    }
    assert not is_capital_action("create_invoice")
    assert not is_capital_action("unknown_action")  # fail-closed: nie 'harmlos'
    assert not hasattr(lc._ActionSpec, "risk_class")  # keine zweite Klassifikation
    assert "irreversible" not in lc._ActionSpec.__dataclass_fields__


def test_m8_receive_allowlist_matches_the_taxonomy() -> None:
    """Die U1-Empfangs-Allowlist der Wert-Schicht ist die dritte Stelle, an der eine
    Aktion 'kapitalfrei' heissen könnte — sie muss aus derselben Klasse folgen."""
    assert set(RECEIVE_ACTIONS) == {
        action for action, klass in ACTION_RISK_CLASSES.items() if klass == "receive"
    }


def test_m8_allowlisted_but_unclassified_action_is_denied() -> None:
    decision = evaluate_policy(
        "exotic_new_action",
        amount_sat=0,
        recipient=None,
        spent_today_sat=0,
        available_balance_sat=1_000_000,
        envelope=_envelope(allowed_actions=frozenset({"exotic_new_action"})),
    )
    assert decision.decision == "denied" and "unclassified" in decision.reason


# --------------------------------------------------------------------------- #
# M-9 / BL-2 — asymmetrisches Fail-Verhalten: Spend denied, Receive überlebt.
# --------------------------------------------------------------------------- #


def _break_money_journal() -> Path:
    """Ein unverkettetes Legacy-Row + abgerissener Tail = maximal kaputtes Journal."""
    path = ln_ops_v2_path()
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


@pytest.mark.parametrize(
    "call",
    [
        lambda cfg: vl.pay_invoice(
            payment_request="lnbc10u1x", dry_run=False, confirm=True, cfg=cfg
        ),
        lambda cfg: vl.keysend(
            dest_pubkey_hex="02abababababababababababababababababababababababababababababababab", amt_sat=10, dry_run=False, confirm=True, cfg=cfg
        ),
        lambda cfg: vl.send_coins(addr="bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4", amount_sat=10, dry_run=False, confirm=True, cfg=cfg),
        lambda cfg: vl.open_channel(
            node_pubkey_hex="02abababababababababababababababababababababababababababababababab", local_funding_sat=10, dry_run=False, confirm=True, cfg=cfg
        ),
        lambda cfg: vl.close_channel(
            funding_txid="ab", output_index=0, dry_run=False, confirm=True, cfg=cfg
        ),
    ],
)
async def test_spend_with_broken_money_journal_is_denied_without_touching_the_node(call) -> None:
    """GEFORDERTER BEWEIS 1: kaputtes v2-Journal ⇒ jeder Spend fail-closed, Node nie
    berührt. Ein Sat, den wir nicht buchen können, darf sich nicht bewegen."""
    _break_money_journal()
    with patch("app.lightning.value_layer._build_client") as build:
        result = await call(_ln_cfg(pay_enabled=True))
    assert result.state == "error"
    assert "money journal" in result.detail and "node not touched" in result.detail
    assert result.intent_id == ""
    build.assert_not_called()


async def test_spend_denied_when_v2_is_missing_but_legacy_v1_still_has_rows(monkeypatch) -> None:
    """Deploy ohne Migration: eine frische Genesis-Kette würde die Geldhistorie
    forken UND das Tages-Cap still auf 0 zurücksetzen. Also: erst migrieren."""
    append_ln_op("pay_invoice", "executed", plan={"payment_request": "lnbc250u1x"})
    assert not ln_ops_v2_path().exists()
    with patch("app.lightning.value_layer._build_client") as build:
        result = await vl.send_coins(
            addr="bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4", amount_sat=10, dry_run=False, confirm=True, cfg=_ln_cfg(pay_enabled=True)
        )
    assert result.state == "error" and "migration" in result.detail
    build.assert_not_called()


async def test_receive_mints_even_with_a_broken_money_journal_and_a_dead_audit(
    tmp_path, monkeypatch
) -> None:
    """GEFORDERTER BEWEIS 2 (BL-2): der Mint überlebt BEIDE Journale.

    Kaputtes Geldjournal + nicht beschreibbares Receive-Journal ⇒ die Rechnung wird
    trotzdem erzeugt. Ein anonymer Zahler darf nie an unserem Audit scheitern.
    """
    _break_money_journal()
    blocked = tmp_path / "not-a-dir"
    blocked.write_text("occupied", encoding="utf-8")
    monkeypatch.setenv("APP_LN_RECEIVE_LEDGER_PATH", str(blocked / "receive.jsonl"))

    client = MagicMock()
    client.add_invoice = AsyncMock(return_value={"payment_request": "lnbc1", "r_hash": "aa"})
    with patch("app.lightning.value_layer._build_client", return_value=client):
        result = await vl.create_invoice(
            value_sat=10, memo="kai-oracle:x", dry_run=False, cfg=_ln_cfg(receive_enabled=True)
        )
    assert result.state == "executed"
    client.add_invoice.assert_awaited_once()


async def test_m9_mint_never_takes_the_money_journal_lock_or_rescans_it(monkeypatch) -> None:
    """M-9: kein Exklusiv-Lock, kein O(n)-Full-Rescan im öffentlichen Mint-Hotpath.

    Gemessen waren 2000 Mints ≈ 95 s kumulativ mit O(n²)-Wachstum, von aussen
    treibbar. Strukturell statt per Benchmark bewiesen: jede Journal-Maschinerie und
    portalocker selbst explodieren hier — der Mint läuft trotzdem durch.
    """

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("mint path must not touch the money journal")

    for name in ("prepare_ln_intent", "append_ln_outcome", "verify_ln_ops_ledger"):
        monkeypatch.setattr(vl, name, _boom)
    monkeypatch.setattr(ops_ledger.portalocker, "Lock", _boom)

    client = MagicMock()
    client.add_invoice = AsyncMock(return_value={"payment_request": "lnbc1", "r_hash": "aa"})
    with patch("app.lightning.value_layer._build_client", return_value=client):
        result = await vl.create_invoice(
            value_sat=10, dry_run=False, cfg=_ln_cfg(receive_enabled=True)
        )
    assert result.state == "executed"
    assert len(receive_ledger.read_recent_receive_events()) == 1  # genau EINE Audit-Zeile


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
    assert not ln_ops_v2_path().exists()  # das Geldjournal bleibt unberührt


# --- Endpoint-Ebene: derselbe Zustand, zwei Verdikte ------------------------------


def test_cockpit_denies_a_spend_early_when_the_money_journal_is_broken(monkeypatch) -> None:
    """Früh-Deny im Cockpit: der Operator verbrennt weder Idempotenz-Key noch HOTP,
    und die Begründung nennt die reparierbare Ursache."""
    _break_money_journal()
    _patch_cockpit(
        monkeypatch,
        _envelope(
            allowed_actions=frozenset({"send_coins"}),
            per_action_cap_sat=1_000_000,
            daily_cap_sat=1_000_000,
        ),
    )
    monkeypatch.setattr(lc, "_fresh_capital_balance_sat", AsyncMock(return_value=1_000_000))
    r = TestClient(_app()).post(
        _URL, json={"action": "send_coins", "params": {"addr": "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4", "amount_sat": 1000}}
    )
    assert r.json()["policy"]["decision"] == "denied"
    assert "money journal" in r.json()["policy"]["reason"]


def test_public_oracle_mint_returns_402_not_503_with_a_broken_money_journal(
    tmp_path, monkeypatch
) -> None:
    """BL-2 end-to-end über den ECHTEN /oracle-Pfad: kaputtes Geldjournal, echter
    Wert-Schicht-Aufruf (nur der lnd-Client ist gefälscht) ⇒ 402 mit Challenge.
    Vorher: 503 pro anonymer Anfrage + zwei Journal-Zeilen."""
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
    with patch("app.lightning.value_layer._build_client", return_value=node):
        r = TestClient(app, raise_server_exceptions=False).get("/oracle/onchain-facts")
    assert r.status_code == 402, r.text
    assert 'invoice="lnbc10n1challenge"' in r.headers.get("WWW-Authenticate", "")
    node.add_invoice.assert_awaited_once()


# --------------------------------------------------------------------------- #
# M-11 — laute Degradation statt stiller 0.
# --------------------------------------------------------------------------- #


async def test_m11_booking_raises_instead_of_pretending_zero() -> None:
    """Ein grüner Timer mit '0 gebucht' ist von 'niemand hat gezahlt' nicht zu
    unterscheiden — für eine Wahrheits-Plattform die schlechteste Fehlerart."""
    from app.lightning.client import LightningUnavailableError

    failing = MagicMock()
    failing.list_invoices = AsyncMock(side_effect=LightningUnavailableError("node down"))
    with patch("app.lightning.earnings_booking._build_client", return_value=failing):
        with pytest.raises(EarningsBookingError, match="UNKNOWN"):
            await book_oracle_earnings(cfg=_ln_cfg())


async def test_m11_missing_invoice_credential_is_loud_too() -> None:
    """Genau der C-1-Zustand: ohne Invoice-Credential buchte der Job still 0 — die
    Treasury-Zahl wäre dauerhaft falsch, ohne dass irgendetwas rot wird."""
    cfg = LightningSettings(
        _env_file=None, enabled=True, tls_cert_path="test-tls.pem", macaroon_hex="read"
    )
    with pytest.raises(EarningsBookingError):
        await book_oracle_earnings(cfg=cfg)


async def test_m11_disabled_lightning_is_still_an_honest_zero() -> None:
    assert await book_oracle_earnings(cfg=LightningSettings(_env_file=None, enabled=False)) == 0


def test_m11_timer_script_exits_nonzero_on_a_degraded_run() -> None:
    """Der Betriebs-Beweis: die systemd-Unit wird ROT. Ein grüner Timer über einer
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
        assert asyncio.run(_main()) == 0  # ehrliche Null bleibt grün


# --------------------------------------------------------------------------- #
# MI-2 / m-18 — Lese-Redaktion + ein Geld-Event = eine Panel-Zeile.
# --------------------------------------------------------------------------- #


def test_mi2_read_path_redacts_legacy_secrets(tmp_path) -> None:
    """``/dashboard/api/ln/ops`` liest historische v1-Zeilen, die VOR der
    Writer-Redaktion entstanden — rohe BOLT11, Preimages, Route-Hops."""
    legacy = tmp_path / "v1.jsonl"
    legacy.write_text(
        json.dumps(
            {
                "ts": "2026-07-02T05:46:20+00:00",
                "action": "pay_invoice",
                "state": "error",
                "plan": {"payment_request": "lnbc250u1raw-secret-invoice"},
                "response": {
                    "payment_preimage": "77" * 32,
                    "payment_route": {"total_amt": "25012", "hops": [{"pub_key": "peer-secret"}]},
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rows = read_recent_ln_ops(path=legacy)
    dumped = json.dumps(rows)
    assert "raw-secret-invoice" not in dumped
    assert "77" * 32 not in dumped
    assert "peer-secret" not in dumped and "hops" not in dumped
    # Die Anzeige-Substanz bleibt: Aktion, Zustand, Betrag inkl. Fees.
    assert rows[0]["action"] == "pay_invoice" and rows[0]["state"] == "error"
    assert rows[0]["response"]["route_summary"]["total_amt_sat"] == 25_012


def test_m18_intent_and_outcome_collapse_to_one_panel_row(tmp_path) -> None:
    path = tmp_path / "v2.jsonl"
    plan = {"amount_sat": 1000, "addr": "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"}
    prepare_ln_intent("send_coins", plan=plan, intent_id="i1", path=path, now=_NOW)
    append_ln_outcome("send_coins", "executed", plan=plan, intent_id="i1", path=path, now=_NOW)
    prepare_ln_intent("send_coins", plan=plan, intent_id="open", path=path, now=_NOW)

    rows = read_recent_ln_ops(path=path)
    assert len(rows) == 2, "zwei Geld-Events, nicht drei Journal-Zeilen"
    assert [row["state"] for row in rows] == ["executed", "intent"]
    assert [row["intent_id"] for row in rows] == ["i1", "open"]


def test_read_path_follows_the_cutover_to_v2_and_falls_back_to_v1(monkeypatch) -> None:
    append_ln_op("keysend", "executed", plan={"amt_sat": 7})
    assert [row["action"] for row in read_recent_ln_ops()] == ["keysend"]  # v2 fehlt → v1
    prepare_ln_intent("send_coins", plan={"amount_sat": 1}, intent_id="v2-row")
    assert [row["action"] for row in read_recent_ln_ops()] == ["send_coins"]  # v2 da → v2


def test_no_app_module_writes_the_frozen_v1_journal_after_the_cutover() -> None:
    """v1 ist eingefroren: die Rollback-Fläche darf existieren, aber niemand schreibt
    parallel — zwei fortgeschriebene Journale wären zwei Geldhistorien."""
    app_dir = Path(vl.__file__).resolve().parents[1]
    offenders = [
        path.relative_to(app_dir).as_posix()
        for path in app_dir.rglob("*.py")
        if path.name != "ops_ledger.py" and "append_ln_op(" in path.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"v1-Writer hat wieder einen Aufrufer: {offenders}"


# --------------------------------------------------------------------------- #
# m-13 / m-14 — Reihenfolge und EINE Fehlschlag-Semantik.
# --------------------------------------------------------------------------- #


def test_m13_envelope_denial_is_not_masked_by_capital_side_denials(monkeypatch) -> None:
    """Reihenfolge wie #638: ist die Aktion gar nicht erlaubt, bleibt GENAU DAS die
    Begründung — sonst jagt der Operator Node-Gesundheit, während die Envelope sperrt."""
    _break_money_journal()
    _patch_cockpit(monkeypatch, PolicyEnvelope.default())  # erlaubt nichts
    monkeypatch.setattr(lc, "_fresh_capital_balance_sat", AsyncMock(return_value=None))
    r = TestClient(_app()).post(
        _URL, json={"action": "send_coins", "params": {"addr": "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4", "amount_sat": 1000}}
    )
    reason = r.json()["policy"]["reason"]
    assert reason == "action not allowed: send_coins"
    assert "stale" not in reason and "money journal" not in reason


def test_m14_one_rule_for_an_unproven_outcome(tmp_path) -> None:
    """EINE Entscheidung, an einer Stelle dokumentiert: ``error`` = Ausgang unbekannt.

    Cap: zählt (Budget nie zweimal ausgeben). Dedup: erlaubt den Retry (lnd selbst
    verhindert die Doppelzahlung derselben payment_hash; ewig sperren würde eine
    ehrlich fehlgeschlagene Rechnung dauerhaft unbezahlbar machen — reproduziertes M-4).
    """
    assert ops_ledger.UNPROVEN_OUTCOME_COUNTS_IN_CAP is True
    assert ops_ledger.UNPROVEN_OUTCOME_ALLOWS_RETRY is True

    path = tmp_path / "v2.jsonl"
    plan = {"amount_sat": 5000, "payment_hash": "ab" * 32, "payment_request": "lnbc50u1x"}
    prepare_ln_intent("pay_invoice", plan=plan, intent_id="first", path=path, now=_NOW)
    append_ln_outcome("pay_invoice", "error", plan=plan, intent_id="first", path=path, now=_NOW)

    assert spent_today_sat_v2(path, now=_NOW) == 5000  # Cap: zählt
    retry = prepare_ln_intent(
        "pay_invoice", plan=plan, intent_id="second", path=path, now=_NOW + timedelta(minutes=1)
    )
    assert retry["intent_id"] == "second"  # Dedup: Retry erlaubt
    assert spent_today_sat_v2(path, now=_NOW) == 10_000  # und der Retry reserviert erneut


def test_reserved_params_cannot_forge_the_authorization_record(monkeypatch) -> None:
    """Die Autorisierungs-Zeile im Geldjournal muss die Zeremonie abbilden, die
    wirklich stattfand — ein per ``params`` untergeschobenes ``authorization`` wäre
    eine Lüge im Audit-Trail."""
    _patch_cockpit(monkeypatch, _envelope(allowed_actions=frozenset({"create_invoice"})))
    r = TestClient(_app()).post(
        _URL,
        json={
            "action": "create_invoice",
            "params": {"value_sat": 1, "authorization": {"confirmation": "hotp"}},
        },
    )
    assert r.status_code == 422 and "reserved params" in r.json()["detail"]
