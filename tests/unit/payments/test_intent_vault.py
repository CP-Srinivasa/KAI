"""Der verschluesselte Sidecar, der einen Intent einen Neustart ueberleben laesst.

**Der Befund, den diese Datei schliesst** (LIVE-Fenster 2026-09-04): das
Journal traegt die Destination nur als Hash — mit Absicht, denn ein BOLT11 im
hash-verketteten Geldjournal waere ein Ziel, das man nie wieder loswird. Die
Rohdestination lebte deshalb ausschliesslich im Prozessspeicher, und nach jedem
``systemctl restart kai-server`` antwortete ``execute`` mit *"unknown intent"*.
Der Operator musste jeden Intent neu anlegen — mitten in einem Fenster mit
scharfem Geldpfad.

Der Vault loest das, ohne die Redaktionsgrenze aufzuweichen: die Rohfelder
liegen AES-256-GCM-verschluesselt in einer eigenen, append-only Datei mit
``0600``. Das Journal bleibt unveraendert die Wahrheit ueber den ZUSTAND; der
Vault traegt nur das Material, das man zum Senden braucht.

Vier Zusagen werden hier gehalten:

1. Was auf der Platte liegt, enthaelt die BOLT11 nicht im Klartext.
2. Ein falscher Schluessel oeffnet nichts — und schweigt nicht darueber.
3. Ein Eintrag laesst sich nicht auf einen anderen Vorgang umhaengen (AAD).
4. Die Datei waechst nur; ein Eintrag wird nie ueberschrieben.
"""

from __future__ import annotations

import base64
import json
import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.payments.enums import PaymentMode
from app.payments.intent_vault import (
    INTENT_VAULT_FILENAME,
    IntentVault,
    IntentVaultError,
)
from app.payments.models import Money, PaymentIntent
from app.payments.rail import DecodedDestination

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
BOLT11 = "lnbc10u1p3xexamplerealisticpaymentrequeststring"
KEY_A = base64.b64encode(b"A" * 32).decode()
KEY_B = base64.b64encode(b"B" * 32).decode()


def key(raw: str) -> bytes:
    return base64.b64decode(raw)


def sat(amount: int) -> Money:
    return Money(minor_units=amount, currency="SAT", scale=0)


def an_intent(intent_id: str = "pi_0123456789abcdef", destination: str = BOLT11) -> PaymentIntent:
    return PaymentIntent(
        intent_id=intent_id,
        idempotency_key="idem-key-0123456789",
        correlation_id="corr-1",
        actor="operator",
        purpose="self_test",
        rail="lightning",
        destination=destination,
        amount_requested=sat(1000),
        fee_limit=sat(5),
        created_at=NOW,
        expires_at=NOW + timedelta(hours=1),
        mode=PaymentMode.LIVE,
    )


def a_binding() -> DecodedDestination:
    return DecodedDestination(
        rail="lightning",
        kind="ln_invoice",
        payee_hash="c" * 64,
        rail_dedup_key="d" * 64,
        amount=sat(1000),
        expires_at=NOW + timedelta(hours=1),
        memo_hash="e" * 64,
    )


def a_vault(tmp_path: Path, raw_key: str = KEY_A) -> IntentVault:
    return IntentVault(tmp_path / INTENT_VAULT_FILENAME, key=key(raw_key))


# --------------------------------------------------------------------------- #
# Versiegeln und oeffnen
# --------------------------------------------------------------------------- #


def test_a_sealed_intent_comes_back_whole(tmp_path: Path) -> None:
    vault = a_vault(tmp_path)
    vault.seal(an_intent(), decoded=a_binding(), moment=NOW)

    restored = a_vault(tmp_path).load()
    entry = restored["pi_0123456789abcdef"]
    assert entry.intent.destination == BOLT11
    assert entry.intent.idempotency_key == "idem-key-0123456789"
    assert entry.intent.amount_requested == sat(1000)
    assert entry.decoded is not None and entry.decoded.rail_dedup_key == "d" * 64


def test_the_binding_survives_because_the_dedup_key_must_not_change(tmp_path: Path) -> None:
    """Ohne die gebundene Destination traegt ein Retry einen ANDEREN Rail-Schluessel.

    ``dedup_key_for`` nimmt den Schluessel aus dem Decode, sonst einen Ersatz aus
    der Destination. Wuerde der Vault das Decode-Ergebnis weglassen, waere der
    Vorgang nach einem Neustart unter einem zweiten Schluessel unterwegs — und
    genau daran haengt die Rail-Dedup.
    """
    vault = a_vault(tmp_path)
    vault.seal(an_intent(), decoded=a_binding(), moment=NOW)
    entry = a_vault(tmp_path).load()["pi_0123456789abcdef"]
    assert entry.decoded is not None
    assert entry.decoded.payee_hash == "c" * 64


def test_an_intent_without_a_binding_is_sealed_too(tmp_path: Path) -> None:
    vault = a_vault(tmp_path)
    vault.seal(an_intent(), decoded=None, moment=NOW)
    entry = a_vault(tmp_path).load()["pi_0123456789abcdef"]
    assert entry.decoded is None
    assert entry.intent.destination == BOLT11


# --------------------------------------------------------------------------- #
# Was auf der Platte liegt
# --------------------------------------------------------------------------- #


def test_the_file_never_carries_the_payment_request_in_the_clear(tmp_path: Path) -> None:
    vault = a_vault(tmp_path)
    vault.seal(an_intent(), decoded=a_binding(), moment=NOW)

    raw = (tmp_path / INTENT_VAULT_FILENAME).read_bytes()
    assert BOLT11.encode() not in raw
    assert b"lnbc" not in raw
    assert b"idem-key" not in raw
    record = json.loads(raw.decode())
    assert set(record) == {"schema", "intent_id", "ts", "nonce", "ciphertext"}
    assert record["intent_id"] == "pi_0123456789abcdef"


def test_the_vault_only_ever_grows(tmp_path: Path) -> None:
    vault = a_vault(tmp_path)
    vault.seal(an_intent("pi_aaaaaaaaaaaaaaaa"), decoded=None, moment=NOW)
    vault.seal(an_intent("pi_bbbbbbbbbbbbbbbb"), decoded=None, moment=NOW)

    lines = (tmp_path / INTENT_VAULT_FILENAME).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert set(a_vault(tmp_path).load()) == {"pi_aaaaaaaaaaaaaaaa", "pi_bbbbbbbbbbbbbbbb"}


@pytest.mark.skipif(os.name != "posix", reason="Dateimodi gibt es nur auf POSIX")
def test_the_vault_lies_on_disk_with_0600(tmp_path: Path) -> None:
    vault = a_vault(tmp_path)
    vault.seal(an_intent(), decoded=None, moment=NOW)
    mode = stat.S_IMODE((tmp_path / INTENT_VAULT_FILENAME).stat().st_mode)
    assert mode == 0o600, f"erwartet 0600, gefunden {mode:04o}"


# --------------------------------------------------------------------------- #
# Fail-closed
# --------------------------------------------------------------------------- #


def test_a_wrong_key_opens_nothing_and_says_so(tmp_path: Path) -> None:
    """Fail-closed, nicht fail-quiet.

    Ein Vault, der bei falschem Schluessel ein leeres Ergebnis zurueckgibt,
    saehe aus wie ein Vault ohne offene Vorgaenge — und der Operator wuerde die
    Intents neu anlegen, statt den Schluessel zu suchen.
    """
    a_vault(tmp_path).seal(an_intent(), decoded=None, moment=NOW)
    with pytest.raises(IntentVaultError, match="cannot be opened"):
        a_vault(tmp_path, KEY_B).load()


def test_an_entry_cannot_be_moved_to_another_intent(tmp_path: Path) -> None:
    """Der Vorgangsschluessel ist mitauthentifiziert (AAD).

    Ohne diese Bindung koennte jemand mit Schreibrecht auf die Datei das Ziel
    eines fremden Vorgangs unter die eigene ID haengen — die Datei bliebe
    formal gueltig, und der naechste Start haette ein anderes Ziel geladen.
    """
    path = tmp_path / INTENT_VAULT_FILENAME
    a_vault(tmp_path).seal(an_intent(), decoded=None, moment=NOW)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["intent_id"] = "pi_ffffffffffffffff"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    with pytest.raises(IntentVaultError, match="cannot be opened"):
        a_vault(tmp_path).load()


def test_a_torn_line_is_a_finding_not_a_shrug(tmp_path: Path) -> None:
    path = tmp_path / INTENT_VAULT_FILENAME
    a_vault(tmp_path).seal(an_intent(), decoded=None, moment=NOW)
    path.write_text(path.read_text(encoding="utf-8") + '{"schema": "broken"', encoding="utf-8")

    with pytest.raises(IntentVaultError):
        a_vault(tmp_path).load()


def test_a_key_of_the_wrong_size_is_refused_before_anything_is_written(tmp_path: Path) -> None:
    with pytest.raises(IntentVaultError, match="32 bytes"):
        IntentVault(tmp_path / INTENT_VAULT_FILENAME, key=b"too-short")


def test_loading_a_vault_that_does_not_exist_yet_is_empty_not_an_error(tmp_path: Path) -> None:
    assert a_vault(tmp_path).load() == {}


def test_a_later_entry_wins_over_an_earlier_one_for_the_same_intent(tmp_path: Path) -> None:
    """Append-only heisst: korrigieren durch Anhaengen, nie durch Ueberschreiben."""
    vault = a_vault(tmp_path)
    vault.seal(an_intent(destination=BOLT11), decoded=None, moment=NOW)
    vault.seal(an_intent(destination=BOLT11 + "x"), decoded=None, moment=NOW)
    entry = a_vault(tmp_path).load()["pi_0123456789abcdef"]
    assert entry.intent.destination == BOLT11 + "x"
