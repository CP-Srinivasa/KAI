"""Belegt echte Closes, die der generische Return-Cap sonst einfangen würde.

Micro-Caps bewegen sich über Nacht zweistellig — eine Größenordnungs-Schwelle
kann das nicht von einem Feed-Artefakt trennen, ein Blick in die echten Kerzen
schon (Register §5c). Diese Liste hält die geprüften Ausnahmen.

**Identität, nicht Ähnlichkeit.** Die erste Fassung erkannte einen Eintrag an
``symbol`` + ``exit_price``. Das ist zu schwach: dasselbe Symbol kann Monate
später wieder ungefähr denselben Exit-Preis haben, und dann würde ein
vollkommen anderer Close den historischen Freispruch *erben*. Ein Freispruch
darf niemals auf einen künftigen Trade überspringen.

Deshalb identifiziert jeder Eintrag den Close über seine **Ereignis-ID**
(``fill_id``, vom Paper-Engine je Fill vergeben), und ``order_id``, ``symbol``,
``timestamp_utc`` und ``exit_price`` sind anschließend **Integritätsprüfungen**:
Weicht auch nur eines ab, gilt der Eintrag als nicht zutreffend und es gibt
**keinen** Freispruch (fail-closed). Ein Close ohne ``fill_id`` wird nie
freigesprochen.

``evidence_sha256`` ist der SHA-256 über den Evidenztext. Wer den Beleg ändert,
ohne den Hash mitzuführen, bricht ``test_evidence_hash_matches_text`` — der
Beleg lässt sich damit nicht stillschweigend umschreiben.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Toleranz für den Preis-Integritätscheck. Weit enger als jeder legitime
# Preisabstand, absorbiert aber Float-Round-Trip-Rauschen.
_PRICE_TOL: float = 1e-9


@dataclass(frozen=True)
class VerifiedRealClose:
    """Ein Close, der geprüft echt ist — identifiziert über seine Ereignis-ID."""

    fill_id: str
    """PRIMÄRE Identität. Ohne sie kein Freispruch."""

    order_id: str
    symbol: str
    timestamp_utc: str
    exit_price: float
    evidence: str
    evidence_sha256: str

    def evidence_hash_ok(self) -> bool:
        """True, wenn der hinterlegte Hash zum Evidenztext passt."""
        return hashlib.sha256(self.evidence.encode("utf-8")).hexdigest() == self.evidence_sha256


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_CYS_EVIDENCE = (
    "2026-08-11 09:28:30Z CYS/USDT long, +38,82 % nach 30 h Haltedauer. "
    "Roh-Preis 1,3904 = exit 1,3897048 / (1 - 0,0005); Bybit 1h-Kerze 09:00Z "
    "low 1,3528 high 1,4077 — der Preis lag in der Kerze der Schliessungsstunde."
)
_SLX_EVIDENCE = (
    "2026-06-27 15:16:26Z SLX/USDT long, +28,19 % nach 27 h Haltedauer. "
    "Roh-Preis 0,4941 = exit 0,49385295 / (1 - 0,0005); Bybit 1h-Kerze 15:00Z "
    "low 0,477 high 0,497 — der Preis lag in der Kerze der Schliessungsstunde."
)
_VELVET_EVIDENCE = (
    "2026-06-29 04:49:25Z VELVET/USDT long, -21,18 % nach 17 h Haltedauer. "
    "Roh-Preis 1,4021 = exit 1,40139895 / (1 - 0,0005); Bybit 1h-Kerze 04:00Z "
    "low 1,35538 high 1,95335 — der Preis lag in der Kerze der Schliessungsstunde."
)

# Geprüft 2026-08-18/19. Verfahren für jeden neuen Eintrag (NICHT abkürzen):
#   1. Slippage aus dem gebuchten ``exit_price`` herausrechnen,
#   2. den Roh-Preis gegen die Kerze der Schliessungsstunde auf der Venue halten,
#   3. Identität (fill_id/order_id/timestamp) aus derselben Audit-Zeile übernehmen,
#   4. Evidenztext schreiben und seinen SHA-256 eintragen.
# „Liegt über der Schwelle" ist kein Beleg — und die 1h-Kerze belegt Markt-
# plausibilität, nicht die Herkunft aus der Ausführungskette. Sobald der
# automatische Close-Verifier steht, ersetzt dessen Urteil dieses Verfahren.
VERIFIED_REAL_CLOSES: tuple[VerifiedRealClose, ...] = (
    VerifiedRealClose(
        fill_id="fill_fbd5580fab5c",
        order_id="ord_986917c2f200",
        symbol="CYS/USDT",
        timestamp_utc="2026-08-11T09:28:30.842264+00:00",
        exit_price=1.3897048,
        evidence=_CYS_EVIDENCE,
        evidence_sha256=_sha(_CYS_EVIDENCE),
    ),
    VerifiedRealClose(
        fill_id="fill_f83be51981e1",
        order_id="ord_282ff031fd9a",
        symbol="SLX/USDT",
        timestamp_utc="2026-06-27T15:16:26.605879+00:00",
        exit_price=0.49385295,
        evidence=_SLX_EVIDENCE,
        evidence_sha256=_sha(_SLX_EVIDENCE),
    ),
    VerifiedRealClose(
        fill_id="fill_446f84adb9e4",
        order_id="ord_d4a7ed829182",
        symbol="VELVET/USDT",
        timestamp_utc="2026-06-29T04:49:25.582030+00:00",
        exit_price=1.40139895,
        evidence=_VELVET_EVIDENCE,
        evidence_sha256=_sha(_VELVET_EVIDENCE),
    ),
)

_BY_FILL_ID: dict[str, VerifiedRealClose] = {rec.fill_id: rec for rec in VERIFIED_REAL_CLOSES}


def _as_float(value: object) -> float | None:
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if out != out else out


def verified_real_close(close_row: dict[str, object]) -> VerifiedRealClose | None:
    """Der Prüfvermerk, wenn dieser Close belegt echt ist — sonst None.

    Fail-closed in jeder Richtung: ohne ``fill_id`` kein Freispruch, und wenn die
    Identität zwar trifft, aber Symbol/Order/Zeit/Preis abweichen, ebenfalls
    keiner — dann stimmt etwas nicht, und Quarantäne ist die sichere Antwort.
    """
    fill_id = str(close_row.get("fill_id") or "").strip()
    if not fill_id:
        return None
    record = _BY_FILL_ID.get(fill_id)
    if record is None:
        return None

    exit_price = _as_float(close_row.get("exit_price"))
    mismatches = []
    if str(close_row.get("symbol") or "").strip() != record.symbol:
        mismatches.append("symbol")
    if str(close_row.get("order_id") or "").strip() != record.order_id:
        mismatches.append("order_id")
    if str(close_row.get("timestamp_utc") or "").strip() != record.timestamp_utc:
        mismatches.append("timestamp_utc")
    if exit_price is None or abs(exit_price - record.exit_price) > _PRICE_TOL:
        mismatches.append("exit_price")

    if mismatches:
        # Sichtbar machen: entweder wurde der Audit-Stream verändert, oder eine
        # fill_id wurde wiederverwendet. Beides gehört gesehen, nicht verschwiegen.
        logger.warning(
            "verified_real_close: Identität %s trifft, aber %s weicht ab — kein Freispruch",
            fill_id,
            "/".join(mismatches),
        )
        return None
    return record


def is_verified_real_close(close_row: dict[str, object]) -> bool:
    """True, wenn dieser Close belegt echt ist."""
    return verified_real_close(close_row) is not None


__all__ = [
    "VERIFIED_REAL_CLOSES",
    "VerifiedRealClose",
    "is_verified_real_close",
    "verified_real_close",
]
