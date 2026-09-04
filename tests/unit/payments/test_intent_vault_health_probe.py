"""Der Konsument des Vault-Sidecars (Stream-Vertrag G4 + ADR 0018 §5/§10).

Der Vault traegt keine Wahrheit ueber Geld. Sein Ausfall kostet deshalb keine
Wertbewegung — er kostet genau das, was im LIVE-Fenster 2026-09-04 gefehlt hat:
ein freigegebener Intent ueberlebt den naechsten Neustart nicht, und der
Operator legt ihn mitten im scharfen Fenster neu an.

Deshalb ``warning`` und nicht ``critical``, und deshalb ein Waechter statt
einer Freshness-Zeile: der Strom ist ereignisgetrieben, in SIMULATION legitim
tagelang still, und was kaputtgehen kann, ist nicht seine Kadenz, sondern seine
**Deckung** — ein offener Vorgang ohne Eintrag.

Die Sonde arbeitet **ohne** ``APP_PAYMENT_VAULT_KEY``. Sie prueft die Form
einer Zeile, nie ihren Inhalt; ein Health-Check, der Schluesselmaterial
braucht, waere ein zweiter Ort, an dem es liegt.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.alerts.health_check import _check_payment_journal_chain
from app.alerts.health_check_payments import check_payment_intent_vault
from app.payments.enums import PaymentMode
from app.payments.intent_vault import INTENT_VAULT_FILENAME, IntentVault
from app.payments.journal import PaymentJournal
from app.payments.models import Money, PaymentIntent

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
KEY = b"p" * 32


def sat(amount: int) -> Money:
    return Money(minor_units=amount, currency="SAT", scale=0)


def an_intent(intent_id: str) -> PaymentIntent:
    return PaymentIntent(
        intent_id=intent_id,
        idempotency_key="idem-key-0123456789",
        correlation_id="corr-1",
        actor="operator",
        purpose="self_test",
        rail="lightning",
        destination="lnbc10u1pexample",
        amount_requested=sat(1000),
        fee_limit=sat(5),
        created_at=NOW,
        expires_at=NOW + timedelta(hours=1),
        mode=PaymentMode.LIVE,
    )


def a_journal(adir: Path) -> PaymentJournal:
    journal = PaymentJournal(adir / "payments" / "payment_journal.jsonl")
    journal.open()
    return journal


def a_vault(adir: Path) -> IntentVault:
    return IntentVault(adir / "payments" / INTENT_VAULT_FILENAME, key=KEY)


def an_awaiting_intent(adir: Path, intent_id: str) -> None:
    journal = a_journal(adir)
    journal.append(intent_id, "intent_created", {"status": "REQUESTED"}, ts=NOW)
    journal.append(intent_id, "policy_decided", {"status": "AWAITING_APPROVAL"}, ts=NOW)


def test_a_missing_vault_is_not_an_issue(tmp_path: Path) -> None:
    """Eine Anlage, die noch nie einen Intent angelegt hat, ist kein Defekt."""
    assert check_payment_intent_vault(tmp_path) == []


def test_a_covered_intent_is_not_an_issue(tmp_path: Path) -> None:
    an_awaiting_intent(tmp_path, "pi_covered00000001")
    a_vault(tmp_path).seal(an_intent("pi_covered00000001"), decoded=None, moment=NOW)
    assert check_payment_intent_vault(tmp_path) == []


def test_an_open_intent_without_a_vault_entry_is_a_warning(tmp_path: Path) -> None:
    """Genau der Befund: dieser Vorgang ueberlebt den naechsten Neustart nicht."""
    an_awaiting_intent(tmp_path, "pi_uncovered000001")
    a_vault(tmp_path).seal(an_intent("pi_somethingelse1"), decoded=None, moment=NOW)

    issues = check_payment_intent_vault(tmp_path)
    assert len(issues) == 1
    assert issues[0].severity == "warning"
    assert issues[0].component == "payment_intent_vault"
    assert "pi_uncovered000001" in issues[0].message


def test_a_settled_intent_needs_no_vault_entry(tmp_path: Path) -> None:
    """Nach dem Send traegt der Vault nichts mehr bei — der Weg ist Reconciliation."""
    journal = a_journal(tmp_path)
    journal.append("pi_done0000000001", "intent_created", {"status": "REQUESTED"}, ts=NOW)
    journal.append("pi_done0000000001", "settled", {"status": "SETTLED"}, ts=NOW)
    a_vault(tmp_path).seal(an_intent("pi_other000000001"), decoded=None, moment=NOW)

    assert check_payment_intent_vault(tmp_path) == []


def test_a_malformed_line_is_a_warning_before_the_next_restart(tmp_path: Path) -> None:
    path = tmp_path / "payments" / INTENT_VAULT_FILENAME
    a_vault(tmp_path).seal(an_intent("pi_covered00000001"), decoded=None, moment=NOW)
    path.write_text(path.read_text(encoding="utf-8") + "{not json\n", encoding="utf-8")

    issues = check_payment_intent_vault(tmp_path)
    assert len(issues) == 1
    assert issues[0].severity == "warning"
    assert "line 2" in issues[0].message


def test_an_entry_without_ciphertext_is_malformed(tmp_path: Path) -> None:
    """Eine Zeile mit leerem Chiffrat ist eine Form ohne Inhalt — die Sonde
    darf sie nicht als Deckung zaehlen."""
    path = tmp_path / "payments" / INTENT_VAULT_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"intent_id": "pi_x", "nonce": "", "ciphertext": ""}) + "\n", encoding="utf-8"
    )
    issues = check_payment_intent_vault(tmp_path)
    assert len(issues) == 1
    assert "malformed" in issues[0].message


def test_a_broken_journal_is_reported_once_not_twice(tmp_path: Path) -> None:
    """Zwei Alarme fuer denselben Defekt machen aus einem Befund ein Rauschen.

    Die Kette meldet ``check_payment_journal_chain``; die Vault-Sonde schweigt
    dazu und meldet nur, was IHR Strom nicht leisten kann.
    """
    journal = a_journal(tmp_path)
    journal.append("pi_1", "intent_created", {"status": "AWAITING_APPROVAL"}, ts=NOW)
    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write('{"seq":2,"partial')

    assert check_payment_intent_vault(tmp_path) == []
    combined = _check_payment_journal_chain(tmp_path)
    assert [issue.component for issue in combined] == ["payment_journal"]


def test_the_probe_is_wired_into_the_report() -> None:
    """Ein Waechter, den niemand ruft, ist eine Behauptung (Stream-Ratchet G4)."""
    repo_root = Path(__file__).resolve().parents[3]
    source = (repo_root / "app" / "alerts" / "health_check.py").read_text(encoding="utf-8")
    assert "check_payment_intent_vault(adir)" in source
    marker = source.index("def run_health_check_report")
    assert "_check_payment_journal_chain(" in source[marker:]


def test_the_stream_contract_declares_this_probe() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    contracts = json.loads(
        (repo_root / "config" / "stream_contracts.json").read_text(encoding="utf-8")
    )
    entry = contracts["streams"]["intent_vault.jsonl"]
    assert entry["monitoring"] == "alternative_watcher"
    assert entry["watcher"] == "_check_payment_journal_chain"
    assert entry["reader"] == "app/alerts/health_check_payments.py"


def test_the_vault_is_in_the_money_backup() -> None:
    """Ohne den Sidecar ueberlebt nach einem Restore kein freigegebener Intent."""
    repo_root = Path(__file__).resolve().parents[3]
    script = (repo_root / "scripts" / "kai_backup_artifacts.sh").read_text(encoding="utf-8")
    assert script.count('"artifacts/payments/intent_vault.jsonl"') == 2, (
        "der Vault gehoert in DEFAULT_SOURCES UND in MONEY_SOURCES"
    )
