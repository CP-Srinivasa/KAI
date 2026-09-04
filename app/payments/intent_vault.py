"""Der verschluesselte Sidecar des Geld-Journals (ADR 0018 §5/§9/§11).

``artifacts/payments/intent_vault.jsonl`` — append-only, ``0600``,
AES-256-GCM.

**Warum es diese zweite Datei gibt.** Das Journal traegt die Destination nur
als Hash, und das bleibt so: ein BOLT11 in einer hash-verketteten,
nie-rotierten Datei ist ein Ziel, das man nicht mehr loswird, und die
Allowlist in :mod:`app.payments.redaction` ist genau dafuer da. Nur folgte
daraus bis zum LIVE-Fenster 2026-09-04 ein zweiter, unbeabsichtigter Satz: die
Rohdestination lebte ausschliesslich im Prozessspeicher. Nach jedem Neustart
war ein freigegebener Intent nicht mehr sendbar (*"unknown intent"*), und der
Operator legte ihn neu an — im scharfen Geldpfad, unter Zeitdruck.

Der Vault trennt beides sauber:

===================  =========================================================
Journal              **Zustand** einer Wertbewegung. Hash-verkettet, Wahrheit,
                     nie rotiert, nur Hashes.
Vault                **Material**, um einen noch nicht gesendeten Vorgang
                     auszufuehren. Verschluesselt, ohne Kette, ohne Aussage
                     ueber Geld.
===================  =========================================================

**Der Vault ist nie Wahrheit.** Er entscheidet nichts. Welcher Vorgang offen
ist, sagt das Journal; der Vault liefert nur die Rohfelder dazu. Ein Eintrag
ohne offenen Journal-Vorgang wird nie geladen, ein offener Vorgang ohne Eintrag
bleibt offen und geht den Weg ueber die Reconciliation.

**Warum AES-GCM und nicht nur ein Hash-MAC.** Gebraucht wird
Vertraulichkeit *und* Integritaet: die Destination darf nicht lesbar sein, und
sie darf auch nicht unbemerkt austauschbar sein. GCM liefert beides in einem
Schritt. Der Vorgangsschluessel geht als **AAD** mit — ohne diese Bindung
koennte jemand mit Schreibrecht einen fremden Eintrag unter eine andere
Intent-ID haengen, und der naechste Start haette ein anderes Ziel geladen, ohne
dass irgendetwas kaputt aussieht.

**Keine neue Abhaengigkeit.** ``cryptography`` steht bereits in
``requirements.lock`` (via ``google-auth``/``pyjwt``) und wird schon in
``app/core/lightning_settings.py`` benutzt.

**Fail-closed heisst hier: laut.** Ein falscher Schluessel gibt kein leeres
Ergebnis zurueck. Ein leeres Ergebnis saehe aus wie "keine offenen Vorgaenge",
und der Operator wuerde die Intents neu anlegen, statt den Schluessel zu
suchen.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.payments.enums import PaymentMode
from app.payments.journal_fs import fsync_directory, harden_permissions
from app.payments.models import Money, PaymentIntent
from app.payments.rail import DecodedDestination

#: Dateiname des Sidecars — als Konstante, damit Leser sie importieren statt
#: das Literal zu wiederholen (Stream-Vertrag G4).
INTENT_VAULT_FILENAME = "intent_vault.jsonl"

SCHEMA = "payment-intent-vault/v1"

#: AES-256. Kuerzer waere kein Tippfehler, sondern ein anderer Algorithmus.
KEY_LENGTH = 32

#: 96 Bit ist die von GCM vorgesehene Nonce-Laenge; jeder Eintrag bekommt eine
#: frische aus ``os.urandom``. Ein Zaehler waere hier gefaehrlich: die Datei
#: wird von zwei Prozessen beschrieben, und eine wiederholte Nonce bricht GCM
#: vollstaendig.
NONCE_LENGTH = 12


class IntentVaultError(RuntimeError):
    """Der Vault kann seine Zusage nicht halten — Schluessel, Form oder Bindung."""


@dataclass(frozen=True)
class VaultEntry:
    """Was ein Vorgang zum Senden braucht, sobald der Speicher weg ist.

    ``decoded`` ist die Destinations-BINDUNG aus dem Rail-Decode. Sie gehoert
    mit in den Vault, weil ``preview.dedup_key_for`` den Rail-Schluessel aus ihr
    nimmt und sonst auf einen Ersatz aus der Destination faellt. Zwei
    verschiedene Schluessel fuer denselben Vorgang waeren das Ende der
    Rail-Dedup — genau der Mechanismus, der einen zweiten Send verhindert.
    """

    intent: PaymentIntent
    decoded: DecodedDestination | None = None


class IntentVault:
    """Writer und Leser des verschluesselten Sidecars.

    Args:
        path: Zieldatei.
        key: 32 Byte Schluesselmaterial (AES-256).

    Raises:
        IntentVaultError: der Schluessel hat nicht die Laenge, die AES-256
            verlangt. Die Pruefung steht im Konstruktor, damit sie VOR dem
            ersten Schreiben zuschlaegt und nicht erst beim Lesen.
    """

    def __init__(self, path: Path, *, key: bytes) -> None:
        if len(key) != KEY_LENGTH:
            raise IntentVaultError(
                f"payment vault key must be {KEY_LENGTH} bytes (AES-256), got {len(key)} — "
                "a key of another length is a different algorithm, not a shorter password"
            )
        self._path = Path(path)
        self._aead = AESGCM(key)

    @property
    def path(self) -> Path:
        return self._path

    # -- Schreiben ---------------------------------------------------------- #

    def seal(
        self,
        intent: PaymentIntent,
        *,
        decoded: DecodedDestination | None,
        moment: datetime,
    ) -> None:
        """Versiegle die Rohfelder eines Vorgangs und haenge sie an.

        Kein Ueberschreiben, keine Rotation: eine Korrektur ist ein neuer
        Eintrag, und beim Laden gewinnt der letzte. Das ist dieselbe Regel wie
        im Journal — nur ohne Kette, weil dieser Strom nichts beweist.
        """
        plaintext = json.dumps(_plain(intent, decoded), sort_keys=True).encode("utf-8")
        nonce = os.urandom(NONCE_LENGTH)
        ciphertext = self._aead.encrypt(nonce, plaintext, _aad(intent.intent_id))
        record = {
            "schema": SCHEMA,
            "intent_id": intent.intent_id,
            "ts": moment.astimezone(UTC).isoformat(),
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        }
        created = not self._path.exists()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("ab") as handle:
            handle.write(json.dumps(record, sort_keys=True).encode("utf-8") + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        if created:
            fsync_directory(self._path.parent)
            harden_permissions(self._path)

    # -- Lesen -------------------------------------------------------------- #

    def load(self) -> dict[str, VaultEntry]:
        """Alle Eintraege, ``intent_id`` -> Eintrag; der letzte gewinnt.

        Raises:
            IntentVaultError: eine Zeile ist unlesbar, oder ein Eintrag laesst
                sich mit diesem Schluessel nicht oeffnen. Beides ist ein
                Befund, kein leeres Ergebnis.
        """
        if not self._path.is_file():
            return {}
        entries: dict[str, VaultEntry] = {}
        for number, line in enumerate(self._path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            entries.update(self._entry(line, number))
        return entries

    def _entry(self, line: str, number: int) -> dict[str, VaultEntry]:
        try:
            record = json.loads(line)
            intent_id = str(record["intent_id"])
            nonce = base64.b64decode(record["nonce"], validate=True)
            ciphertext = base64.b64decode(record["ciphertext"], validate=True)
        except (ValueError, KeyError, TypeError) as exc:
            raise IntentVaultError(
                f"payment vault line {number} is unreadable ({type(exc).__name__}) — "
                f"repair or move {self._path} aside before the next send"
            ) from exc
        try:
            plaintext = self._aead.decrypt(nonce, ciphertext, _aad(intent_id))
        except InvalidTag as exc:
            raise IntentVaultError(
                f"payment vault line {number} cannot be opened with the configured "
                "APP_PAYMENT_VAULT_KEY — either the key changed or the entry was "
                "tampered with; refusing to guess which"
            ) from exc
        return {intent_id: _entry_from(json.loads(plaintext.decode("utf-8")))}


# --------------------------------------------------------------------------- #
# Serialisierung
# --------------------------------------------------------------------------- #


def _aad(intent_id: str) -> bytes:
    """Die mitauthentifizierten Daten: Schema UND Vorgangsschluessel."""
    return f"{SCHEMA}|{intent_id}".encode()


def _money(amount: Money) -> dict[str, Any]:
    return {
        "minor_units": amount.minor_units,
        "currency": amount.currency,
        "scale": amount.scale,
    }


def _plain(intent: PaymentIntent, decoded: DecodedDestination | None) -> dict[str, Any]:
    return {
        "intent": {
            "intent_id": intent.intent_id,
            "idempotency_key": intent.idempotency_key,
            "correlation_id": intent.correlation_id,
            "actor": intent.actor,
            "purpose": intent.purpose,
            "rail": intent.rail,
            "destination": intent.destination,
            "amount_requested": _money(intent.amount_requested),
            "fee_limit": _money(intent.fee_limit),
            "created_at": intent.created_at.isoformat(),
            "expires_at": intent.expires_at.isoformat(),
            "mode": intent.mode.value,
        },
        "decoded": None
        if decoded is None
        else {
            "rail": decoded.rail,
            "kind": decoded.kind,
            "payee_hash": decoded.payee_hash,
            "rail_dedup_key": decoded.rail_dedup_key,
            "amount": None if decoded.amount is None else _money(decoded.amount),
            "expires_at": None if decoded.expires_at is None else decoded.expires_at.isoformat(),
            "memo_hash": decoded.memo_hash,
        },
    }


def _entry_from(plain: dict[str, Any]) -> VaultEntry:
    raw = plain["intent"]
    intent = PaymentIntent(
        intent_id=raw["intent_id"],
        idempotency_key=raw["idempotency_key"],
        correlation_id=raw["correlation_id"],
        actor=raw["actor"],
        purpose=raw["purpose"],
        rail=raw["rail"],
        destination=raw["destination"],
        amount_requested=Money(**raw["amount_requested"]),
        fee_limit=Money(**raw["fee_limit"]),
        created_at=datetime.fromisoformat(raw["created_at"]),
        expires_at=datetime.fromisoformat(raw["expires_at"]),
        mode=PaymentMode(raw["mode"]),
    )
    bound = plain.get("decoded")
    if not isinstance(bound, dict):
        return VaultEntry(intent=intent)
    return VaultEntry(
        intent=intent,
        decoded=DecodedDestination(
            rail=bound["rail"],
            kind=bound["kind"],
            payee_hash=bound["payee_hash"],
            rail_dedup_key=bound["rail_dedup_key"],
            amount=None if bound["amount"] is None else Money(**bound["amount"]),
            expires_at=(
                None if bound["expires_at"] is None else datetime.fromisoformat(bound["expires_at"])
            ),
            memo_hash=bound["memo_hash"],
        ),
    )


__all__ = [
    "INTENT_VAULT_FILENAME",
    "KEY_LENGTH",
    "SCHEMA",
    "IntentVault",
    "IntentVaultError",
    "VaultEntry",
]
