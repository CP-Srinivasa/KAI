"""Redaktionsgrenze des Geld-Journals (ADR 0017 §9).

Eine **Allowlist**, keine Blocklist. Der Unterschied ist nicht Geschmack: eine
Blocklist muss jedes zukuenftige Geheimnis kennen, eine Allowlist muss nur
wissen, was oeffentlich sein darf. ``ops_ledger._redact_plan`` arbeitet aus
demselben Grund so, und dort ist die Lehre bereits bezahlt — LND-Fehlerstrings
(``payment_error``) koennen ein Ziel zurueckspiegeln und sind deshalb nicht
allowlisted.

Drei Filterstufen, weil ein Schluesselname allein nicht traegt:

1. **Schluessel** muss in :data:`ALLOWED_KEYS` stehen.
2. **Hash-Felder** (``*_hash`` und ``rail_dedup_key``) muessen auch WIE ein
   Hash aussehen. Ein ``destination_hash``, in dem ein BOLT11 steht, ist der
   bequemste Weg an einer Allowlist vorbei.
3. **Freitext** wird gekuerzt und auf Rohmuster geprueft (BOLT11-Praefixe,
   lange Hex-Ketten). Ein Preimage in einem ``failure_reason`` ist immer noch
   ein Preimage.

Was hier durchfaellt, wird **verworfen** — nicht maskiert. Ein maskierter Wert
verleitet dazu, ihn spaeter "nur einmal" wieder aufzumachen.
"""

from __future__ import annotations

import re
from typing import Any

#: Maximale Laenge eines Freitextwerts im Journal.
MAX_TEXT_LENGTH = 128

#: Maximale Anzahl Elemente in einer Liste (rule_ids, reasons).
MAX_LIST_ITEMS = 8

#: Nicht-negative Ganzzahlen bis hierhin; darueber ist es kein Betrag mehr,
#: sondern ein Tippfehler oder ein Angriff auf die Journalgroesse.
MAX_INT = 2**53

#: Was ein Payload enthalten DARF. Alles andere existiert nach dem Append nicht.
ALLOWED_KEYS: frozenset[str] = frozenset(
    {
        # Identitaet und Kontext
        "idempotency_key_hash",
        "correlation_id",
        "actor",
        "purpose",
        "rail",
        "mode",
        "status",
        "replayed",
        # Policy
        "verdict",
        "rule_ids",
        "reasons",
        "policy_refs",
        # Betraege — vier getrennte Groessen (ADR §3)
        "amount_minor_units",
        "amount_sent_minor_units",
        "amount_settled_minor_units",
        "fee_limit_minor_units",
        "fee_estimate_minor_units",
        "fee_actual_minor_units",
        "currency",
        "scale",
        # Rail-Material — ausschliesslich als Hash
        "destination_hash",
        "payee_hash",
        "memo_hash",
        "route_hint_hash",
        "invoice_ref_hash",
        "proof_hash",
        "rail_dedup_key",
        "proof_kind",
        "finality",
        # Ausfuehrung
        "attempt_no",
        "estimate_source",
        "observed_status",
        "evidence_source",
        "failure_reason",
        "approval_counter",
        "expires_at_unix",
    }
)

#: Felder, deren Wert die Form eines SHA-256-Hex haben MUSS.
HASH_KEYS: frozenset[str] = frozenset({"rail_dedup_key"})

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")

#: Rohmuster, die in keinem Freitextfeld etwas zu suchen haben. Die
#: BOLT11-Praefixe decken main/test/regtest/signet ab; 32 Hex-Zeichen am Stueck
#: sind bereits die Haelfte eines Preimage oder Pubkey.
_RAW_PATTERNS = (
    re.compile(r"ln(bc|tb|bcrt|sb)[0-9]", re.IGNORECASE),
    re.compile(r"[0-9a-fA-F]{32,}"),
)


def _is_hash_key(key: str) -> bool:
    return key.endswith("_hash") or key in HASH_KEYS


def _clean_text(key: str, value: str) -> str | None:
    text = value.strip()
    if not text:
        return None
    if _is_hash_key(key):
        lowered = text.lower()
        return lowered if _HASH_RE.match(lowered) else None
    truncated = text[:MAX_TEXT_LENGTH]
    if any(pattern.search(truncated) for pattern in _RAW_PATTERNS):
        return None
    return truncated


def redact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Gib die oeffentliche Fassung eines Payloads zurueck.

    Verwirft alles, was nicht allowlisted, nicht formgerecht oder nicht
    typgerecht ist. Wirft nie: ein Aufrufer, der versehentlich ein Geheimnis
    mitgibt, soll den Append nicht verlieren — er soll das Geheimnis verlieren.
    """
    out: dict[str, Any] = {}
    for key in sorted(payload):
        if key not in ALLOWED_KEYS:
            continue
        value = payload[key]
        if isinstance(value, bool):
            out[key] = value
        elif isinstance(value, int):
            if 0 <= value <= MAX_INT:
                out[key] = value
        elif isinstance(value, str):
            cleaned = _clean_text(key, value)
            if cleaned is not None:
                out[key] = cleaned
        elif isinstance(value, (list, tuple)):
            items = [
                cleaned
                for item in list(value)[:MAX_LIST_ITEMS]
                if isinstance(item, str) and (cleaned := _clean_text(key, item)) is not None
            ]
            if items:
                out[key] = items
    return out


__all__ = ["ALLOWED_KEYS", "MAX_TEXT_LENGTH", "redact_payload"]
