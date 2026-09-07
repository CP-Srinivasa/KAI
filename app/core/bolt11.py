"""BOLT11-Betrag und lnd-Payment-Hash — zwei reine Funktionen, eine Heimat.

Beide lagen in ``app/lightning/ops_ledger.py``. Mit dem Rueckbau des alten
Sendewegs (ADR 0018 §12) faellt dieses Modul auf ein Archiv zusammen, die
Funktionen selbst aber werden weiter gebraucht — und zwar von BEIDEN Seiten der
Grenze, die ADR 0018 §2 zieht:

* ``app.payments.rails.lightning_mapping`` bildet lnd-Antworten auf das
  Domaenenmodell ab und braucht den normalisierten Hash;
* ``app.lightning.receive_ledger`` redigiert den Empfangs-Audit-Trail und
  braucht beide, weil eine ``add_invoice``-Antwort ``r_hash`` (base64) und
  ``payment_request`` (BOLT11) traegt.

``app.lightning`` darf ``app.payments`` nicht importieren (Richtungs-Test), und
eine zweite Kopie waere ein zweiter Katalog derselben Regel — genau die
Defektklasse, gegen die es hier geht. Deshalb liegen sie auf neutralem Boden:
dieses Modul importiert **nichts** aus ``app.*`` (mechanisch geprueft).
"""

from __future__ import annotations

import base64
import binascii
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_BOLT11_HRP_RE = re.compile(r"^ln(?:bc|tb|bcrt)(\d+)([munp]?)1", re.IGNORECASE)
_HRP_MULTIPLIER_MSAT_PER_UNIT = {
    # msat pro HRP-Einheit: 1 BTC = 1e11 msat; m=1e-3, u=1e-6, n=1e-9, p=1e-12 BTC
    "": 100_000_000_000,
    "m": 100_000_000,
    "u": 100_000,
    "n": 100,
    "p": 0,  # Pico unter msat-Granularitaet nur bei nicht-10er-Vielfachen; s. unten
}


def bolt11_amount_sat(payment_request: str) -> int:
    """Betrag (sat) aus dem BOLT11-HRP — 0 wenn amountless/unparsebar.

    Konservativ aufgerundet (ein Cap darf nie durch Abrunden unterlaufen werden).
    """
    match = _BOLT11_HRP_RE.match(payment_request.strip())
    if not match:
        return 0
    digits, unit = int(match.group(1)), match.group(2).lower()
    if unit == "p":
        msat = -(-digits * 100 // 1000)  # p: 1e-12 BTC = 0.1 msat -> ceil auf msat
    else:
        msat = digits * _HRP_MULTIPLIER_MSAT_PER_UNIT[unit]
    return -(-msat // 1000)  # ceil msat -> sat


def normalize_payment_hash(value: Any) -> str:
    """Normalise a payment hash to lowercase hex (MI-1).

    LND speaks base64 (``r_hash`` in the REST JSON) while the pay path and the
    operator speak hex. Storing both forms verbatim would make the M-4 duplicate
    check blind: the same invoice would appear twice under two spellings. Every
    v2 record therefore stores HEX. A value that is neither 64-char hex nor a
    32-byte base64 blob is kept verbatim and logged — dropping it would silently
    disarm the duplicate guard.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) == 64:
        try:
            bytes.fromhex(text)
        except ValueError:
            pass
        else:
            return text.lower()
    candidate = text.replace("-", "+").replace("_", "/")
    padded = candidate + "=" * (-len(candidate) % 4)
    try:
        raw = base64.b64decode(padded, validate=True)
    except (binascii.Error, ValueError):
        raw = b""
    if len(raw) == 32:
        return raw.hex()
    logger.warning("[ln-ops] payment_hash not normalisable to hex — stored verbatim")
    return text


__all__ = ["bolt11_amount_sat", "normalize_payment_hash"]
