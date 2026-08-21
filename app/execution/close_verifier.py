"""Der reine Offline-Verifier für einen Close.

**Kein Netz.** Dieses Modul importiert bewusst keinen HTTP-Client und keinen
Venue-Adapter; ein Architektur-Test hält das fest. Es bekommt genau zwei Dinge —
die Audit-Zeile und ein Evidenz-Artefakt — und erzeugt daraus ein Urteil samt
Begründungscodes. Dieselben Eingaben ergeben immer dasselbe Ergebnis.

**Zwei Evidenzstärken, die nie verwischen dürfen.**

``VERIFIED_MARKET_PLAUSIBLE``
    Der Preis existierte real am Markt. Mehr ist für Legacy-Closes nicht
    erreichbar: vor der Provenienz-Schicht wurde nirgends festgehalten, welcher
    Snapshot den Fill verursacht hat. Keine noch so gute Kerzen-Rekonstruktion
    darf daraus rückwirkend eine Ausführungs-Provenienz machen.

``VERIFIED_EXECUTION_PROVENANCE``
    Die vollständige Kette hält: Identität, zulässige Quelle, beobachteter Preis,
    valide Beobachtungszeit, ``market_data_is_stale is False``, Alter innerhalb
    der Grenze, Tick-Klammer, Referenzpreis, bit-exakte Slippage-Rekonstruktion
    und ein konsistentes Venue-Fenster.

Fehlt ein Pflichtstück, lautet das Urteil ``UNVERIFIED`` **mit Begründungscode** —
nie „wahrscheinlich echt". Die Codes sind der Grund, warum später niemand aus
„37 unverifiziert" schließen kann, die seien halt alt gewesen.

Zur Genauigkeit: die Slippage-Rekonstruktion darf bit-exakt verlangt werden, weil
beide Werte aus derselben Engine-Arithmetik stammen. Der Vergleich des gebuchten
Preises mit *externen* Kerzen darf das nicht — dort gilt ein vorab definiertes
Band.
"""

from __future__ import annotations

import hashlib
import inspect
import sys
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from functools import lru_cache

from app.execution.close_evidence import CloseEvidence, VenueCandle

__all__ = [
    "MAX_QUOTE_AGE_MS",
    "VENUE_BAND_TOLERANCE_PCT",
    "ProvenanceClass",
    "ReasonCode",
    "VerificationResult",
    "VerifierVerdict",
    "verify_close",
    "verifier_code_sha",
]

# Versiegelte Grenzen. Aenderungen sind Kalibrierungs-Entscheidungen, keine
# Nebenwirkungen — sie gehoeren begruendet und gemessen, nicht angepasst, bis ein
# Fall durchgeht.
MAX_QUOTE_AGE_MS: float = 120_000.0
"""Aelter als zwei Minuten heisst: die Quote beschreibt den Fill-Moment nicht mehr."""

VENUE_BAND_TOLERANCE_PCT: float = 0.05
"""Toleranz beim Vergleich mit EXTERNEN Kerzen. Nie bit-exakt fordern."""


class VerifierVerdict(StrEnum):
    VERIFIED_EXECUTION_PROVENANCE = "verified_execution_provenance"
    VERIFIED_MARKET_PLAUSIBLE = "verified_market_plausible"
    QUARANTINE = "quarantine"
    UNVERIFIED = "unverified"


class ProvenanceClass(StrEnum):
    FULL = "full"
    UNAVAILABLE_BY_LEGACY_SCHEMA = "unavailable_by_legacy_schema"


class ReasonCode(StrEnum):
    # fehlende Pflichtangaben
    MISSING_PRICE_SOURCE = "missing_price_source"
    MISSING_OBSERVED_TIMESTAMP = "missing_observed_timestamp"
    MISSING_OBSERVED_MARKET_PRICE = "missing_observed_market_price"
    MISSING_EXECUTION_REFERENCE_PRICE = "missing_execution_reference_price"
    MISSING_TICK_ID = "missing_tick_id"
    MISSING_CLOSE_IDENTITY = "missing_close_identity"
    MISSING_CLOSE_TIMESTAMP = "missing_close_timestamp"
    """Der Close selbst traegt keine verwertbare Zeit — nicht zu verwechseln mit
    ``MISSING_OBSERVED_TIMESTAMP`` (die QUOTE hat keine)."""

    AGE_UNAVAILABLE = "age_unavailable"
    # negative Evidenz
    STALE_MARKET_DATA = "stale_market_data"
    STALE_FLAG_UNKNOWN = "stale_flag_unknown"
    AGE_EXCEEDS_LIMIT = "age_exceeds_limit"
    SYNTHETIC_PRICE_SOURCE = "synthetic_price_source"
    SLIPPAGE_MISMATCH = "slippage_mismatch"
    IDENTITY_CHAIN_MISMATCH = "identity_chain_mismatch"
    OBSERVED_PRICE_OUTSIDE_VENUE_BAND = "observed_price_outside_venue_band"
    # Evidenz-Artefakt
    VENUE_WINDOW_UNAVAILABLE = "venue_window_unavailable"
    VENUE_WINDOW_DOES_NOT_COVER_CLOSE = "venue_window_does_not_cover_close"
    EVIDENCE_HASH_MISMATCH = "evidence_hash_mismatch"
    EVIDENCE_SYMBOL_MISMATCH = "evidence_symbol_mismatch"
    MISSING_EVIDENCE_HASH = "missing_evidence_hash"
    """Ein VERIFIED-Urteil braucht einen EXTERN verankerten Hash. Ohne ihn
    pruefte der Verifier nur gegen den Hash, den er sich selbst aus demselben
    Objekt gerade ausgerechnet hat — das beweist nichts."""

    EVIDENCE_ORDER_MISMATCH = "evidence_order_mismatch"
    EVIDENCE_CLOSE_TIME_MISMATCH = "evidence_close_time_mismatch"
    VENUE_SOURCE_MISMATCH = "venue_source_mismatch"


@dataclass(frozen=True)
class VerificationResult:
    verdict: VerifierVerdict
    provenance_class: ProvenanceClass
    reasons: tuple[ReasonCode, ...] = ()
    evidence_sha256: str = ""
    verifier_code_sha: str = ""

    @property
    def is_verified(self) -> bool:
        return self.verdict in (
            VerifierVerdict.VERIFIED_EXECUTION_PROVENANCE,
            VerifierVerdict.VERIFIED_MARKET_PLAUSIBLE,
        )


@lru_cache(maxsize=1)
def verifier_code_sha() -> str:
    """SHA-256 ueber den Quelltext dieses Moduls.

    Damit haengt an jedem Urteil, WELCHE Regelversion es gefaellt hat. Wer die
    Regeln aendert, aendert den Hash — ein altes Urteil bleibt damit von einem
    neuen unterscheidbar, auch wenn beide "verified" sagen.
    """
    source = inspect.getsource(sys.modules[__name__])
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _as_float(value: object) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    out = float(value)
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


def _parse_ms(timestamp: object) -> int | None:
    if not timestamp:
        return None
    try:
        stamp = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        return None
    return int(stamp.timestamp() * 1000)


def _normalize_source(raw: object) -> str:
    """Anbietername ohne angehaengte Marker.

    ``price_source`` kann Zusaetze tragen (``bybit|provider_disagreement:…``,
    ``mock|synthetic_not_tradeable``). Fuer den Abgleich mit der Venue des
    Artefakts zaehlt der Anbieter davor.
    """
    return str(raw or "").strip().split("|", 1)[0].strip().lower()


def _provenance_class(close_row: dict[str, object]) -> ProvenanceClass:
    """Legacy erkennt man daran, dass die Provenienz-Felder gar nicht existieren."""
    has_any = any(
        str(close_row.get(key, "") or "").strip()
        for key in ("price_source", "price_observed_at_utc", "monitor_tick_id")
    )
    return ProvenanceClass.FULL if has_any else ProvenanceClass.UNAVAILABLE_BY_LEGACY_SCHEMA


def _check_evidence(
    close_row: dict[str, object],
    evidence: CloseEvidence | None,
    expected_sha: str | None,
) -> tuple[list[ReasonCode], VenueCandle | None]:
    """Traegt das Artefakt ueberhaupt zu diesem Close bei?"""
    reasons: list[ReasonCode] = []
    if evidence is None or evidence.is_empty:
        reasons.append(ReasonCode.VENUE_WINDOW_UNAVAILABLE)
        return reasons, None
    if expected_sha and evidence.payload_sha256() != expected_sha:
        reasons.append(ReasonCode.EVIDENCE_HASH_MISMATCH)
        return reasons, None
    if str(evidence.symbol).strip() != str(close_row.get("symbol", "")).strip():
        reasons.append(ReasonCode.EVIDENCE_SYMBOL_MISMATCH)
        return reasons, None
    close_ms = _parse_ms(close_row.get("timestamp_utc"))
    if close_ms is None:
        # Der CLOSE hat keine verwertbare Zeit — eigener Code, damit das nicht
        # mit einer fehlenden Quote-Zeit verwechselt wird.
        reasons.append(ReasonCode.MISSING_CLOSE_TIMESTAMP)
        return reasons, None
    candle = evidence.candle_covering(close_ms)
    if candle is None:
        reasons.append(ReasonCode.VENUE_WINDOW_DOES_NOT_COVER_CLOSE)
        return reasons, None
    return reasons, candle


def verify_close(
    close_row: dict[str, object],
    evidence: CloseEvidence | None = None,
    *,
    expected_evidence_sha256: str | None = None,
    slippage_fraction: float = 0.0005,
) -> VerificationResult:
    """Urteil ueber einen Close aus Audit-Zeile und Evidenz-Artefakt.

    Rein: keine Netzwerkzugriffe, keine Uhr, keine Zufallszahlen. Dieselben
    Eingaben ergeben immer dasselbe Ergebnis.
    """
    provenance = _provenance_class(close_row)
    sha = evidence.payload_sha256() if evidence is not None else ""
    reasons: list[ReasonCode] = []

    def result(verdict: VerifierVerdict) -> VerificationResult:
        return VerificationResult(
            verdict=verdict,
            provenance_class=provenance,
            reasons=tuple(dict.fromkeys(reasons)),
            evidence_sha256=sha,
            verifier_code_sha=verifier_code_sha(),
        )

    # --- Identitaet -----------------------------------------------------------
    if not str(close_row.get("fill_id", "") or "").strip():
        reasons.append(ReasonCode.MISSING_CLOSE_IDENTITY)
    if evidence is not None and evidence.close_fill_id:
        if evidence.close_fill_id != str(close_row.get("fill_id", "") or "").strip():
            reasons.append(ReasonCode.IDENTITY_CHAIN_MISMATCH)
    if evidence is not None and evidence.close_order_id:
        # Ein Artefakt fuer Fill A darf Fill B nicht verifizieren, nur weil Symbol
        # und Kerzenfenster zufaellig passen.
        if evidence.close_order_id != str(close_row.get("order_id", "") or "").strip():
            reasons.append(ReasonCode.EVIDENCE_ORDER_MISMATCH)
    if evidence is not None and evidence.close_timestamp_utc:
        if evidence.close_timestamp_utc != str(close_row.get("timestamp_utc", "") or "").strip():
            reasons.append(ReasonCode.EVIDENCE_CLOSE_TIME_MISMATCH)

    # --- Marktplausibilitaet: gilt fuer BEIDE Klassen -------------------------
    evidence_reasons, candle = _check_evidence(close_row, evidence, expected_evidence_sha256)
    reasons.extend(evidence_reasons)

    exit_price = _as_float(close_row.get("exit_price"))
    observed = _as_float(close_row.get("observed_market_price"))

    # WELCHER Preis gegen den Markt gehalten wird, haengt an der Provenienz-Klasse
    # — sonst haette #746 die Preise umsonst getrennt:
    #
    #   LEGACY : nur ``exit_price`` existiert. Er traegt die Slippage und ist bei
    #            einer Liquidation nicht der beobachtete Marktpreis; mehr ist ohne
    #            Provenienz aber nicht zu haben, und genau deshalb ist der
    #            Hoechststatus dort MARKET_PLAUSIBLE und nicht mehr.
    #   FULL   : ``observed_market_price`` ist der TATSAECHLICH beobachtete
    #            Venue-Preis. Ihn gegen die Kerze zu halten ist die eigentliche
    #            Marktpruefung. Bei einer Liquidation gilt
    #            observed != execution_reference != exit — den exit_price gegen
    #            den Markt zu halten wuerde dort eine Abweichung melden, die es
    #            nicht gibt (oder eine echte verdecken).
    band_price = observed if provenance is ProvenanceClass.FULL else exit_price
    market_ok = False
    if candle is not None and band_price is not None:
        # Externer Vergleich: Band, nicht Bit-Gleichheit.
        market_ok = candle.contains(band_price, tolerance_pct=VENUE_BAND_TOLERANCE_PCT)
        if not market_ok:
            reasons.append(ReasonCode.OBSERVED_PRICE_OUTSIDE_VENUE_BAND)

    # Ein VERIFIED-Urteil braucht einen EXTERN verankerten Hash. Ohne ihn pruefte
    # der Verifier gegen den Hash, den er sich gerade selbst aus demselben Objekt
    # ausgerechnet hat — das ist keine Verankerung, sondern ein Zirkelschluss.
    if evidence is not None and not expected_evidence_sha256:
        reasons.append(ReasonCode.MISSING_EVIDENCE_HASH)

    if provenance is ProvenanceClass.UNAVAILABLE_BY_LEGACY_SCHEMA:
        # Hoechststatus fuer Legacy — mehr ist ohne Provenienz nicht beweisbar.
        if market_ok and not reasons:
            return result(VerifierVerdict.VERIFIED_MARKET_PLAUSIBLE)
        return result(VerifierVerdict.UNVERIFIED)

    # --- Vollstaendige Kette (nur neue Closes) --------------------------------
    source = str(close_row.get("price_source", "") or "").strip()
    if not source:
        reasons.append(ReasonCode.MISSING_PRICE_SOURCE)
    elif source.startswith("mock"):
        # Synthetik ist kein Anbieter. Das ist negative Evidenz, kein Mangel.
        reasons.append(ReasonCode.SYNTHETIC_PRICE_SOURCE)
        return result(VerifierVerdict.QUARANTINE)

    if evidence is not None and evidence.venue and source:
        if _normalize_source(source) != str(evidence.venue).strip().lower():
            # Die Evidenz stammt von einer anderen Venue als der Preis.
            reasons.append(ReasonCode.VENUE_SOURCE_MISMATCH)
    if not str(close_row.get("price_observed_at_utc", "") or "").strip():
        reasons.append(ReasonCode.MISSING_OBSERVED_TIMESTAMP)
    if _as_float(close_row.get("observed_market_price")) is None:
        reasons.append(ReasonCode.MISSING_OBSERVED_MARKET_PRICE)
    reference = _as_float(close_row.get("execution_reference_price"))
    if reference is None:
        reasons.append(ReasonCode.MISSING_EXECUTION_REFERENCE_PRICE)
    if not str(close_row.get("monitor_tick_id", "") or "").strip():
        reasons.append(ReasonCode.MISSING_TICK_ID)

    stale = close_row.get("market_data_is_stale")
    if stale is None:
        # Fehlende Evidenz — anderer Grund als "war stale", deshalb eigener Code.
        reasons.append(ReasonCode.STALE_FLAG_UNKNOWN)
    elif stale is True:
        reasons.append(ReasonCode.STALE_MARKET_DATA)

    age = _as_float(close_row.get("market_data_age_ms"))
    if age is None:
        reasons.append(ReasonCode.AGE_UNAVAILABLE)
    elif age > MAX_QUOTE_AGE_MS:
        reasons.append(ReasonCode.AGE_EXCEEDS_LIMIT)

    # Interne Rekonstruktion: hier IST Bit-Gleichheit zulaessig, weil beide Werte
    # aus derselben Engine-Arithmetik stammen.
    if reference is not None and exit_price is not None:
        side = str(close_row.get("position_side", "long") or "long")
        factor = 1.0 + slippage_fraction if side == "short" else 1.0 - slippage_fraction
        if reference * factor != exit_price:
            reasons.append(ReasonCode.SLIPPAGE_MISMATCH)

    if reasons:
        return result(VerifierVerdict.UNVERIFIED)
    if not market_ok:
        return result(VerifierVerdict.UNVERIFIED)
    return result(VerifierVerdict.VERIFIED_EXECUTION_PROVENANCE)
