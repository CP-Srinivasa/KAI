"""Praeregistrierung in zwei Stufen: Candidate (jetzt) und Activation (spaeter).

Der uebliche Fehler waere, heute eine Datei anzulegen und spaeter ``T0``
hineinzueditieren — und dann zu behaupten, es sei dieselbe Praeregistrierung.
Das ist sie nicht: eine Datei, die sich noch aendert, ist nicht versiegelt.

Deshalb zwei unveraenderliche Artefakte, die eine Kette bilden::

    CANDIDATE  (jetzt)     alle Forschungsparameter, T0 = NOT_SET
        |                  -> PREREG_CANDIDATE_SHA256
        v
    ACTIVATION (spaeter)   verweist auf candidate_sha256, setzt T0/T1/T2,
                           traegt Code- und Evaluator-SHA
                           -> PREREG_ACTIVATION_SHA256

Der Candidate wird **jetzt** geschlossen, waehrend noch kein einziges Outcome
gesehen wurde. Die OOS-Uhr startet erst mit der Activation — und die hat
operative Vorbedingungen (Pi-P0), keine statistischen. Damit ist sauber
getrennt, was eine Forschungsentscheidung ist und was ein Betriebszustand.

Nach der Activation ist **nichts** davon mehr aenderbar. Insbesondere die Kosten:
sie stehen als Zahl im Candidate und werden nicht zur Verdikt-Zeit aus einer
Konfiguration gelesen. Sonst koennte sich in 90 Tagen eine Einstellung aendern
und dieselben Trades wuerden an einer anderen Huerde gemessen.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

PREREG_SPEC_VERSION = "kai/prereg/v2"

STATUS_CANDIDATE = "CANDIDATE_LOCKED_NOT_ACTIVE"
STATUS_ACTIVE = "ACTIVE"

# Kosten und Huerde. Beide bindend ab Activation; die eigentliche oekonomische
# Anforderung ist damit ein Brutto-Edge von >= 25 bps je Signal.
PREREG_ROUND_TRIP_COST_BPS = 20.0
PREREG_ECONOMIC_FLOOR_BPS = 5.0

# NICHT gatend. Eine Sensitivitaetsanalyse, kein Alternativ-Gate: hinterher
# "bei 20 bps hat es nicht gereicht, bei einem anderen Kostenmodell schon" zu
# sagen, waere nachtraegliche Kriterienaenderung. Die versiegelten 20 bps
# entscheiden.
SENSITIVITY_COST_BPS: tuple[float, ...] = (20.0, 25.0, 30.0)

# Ebenfalls NICHT gatend. CR1 kann gegenueber einem sehr grossen Einzelcluster
# empfindlich sein; das soll man SEHEN, ohne dass es das versiegelte Verdikt
# rueckwirkend aendert.
ROBUSTNESS_DIAGNOSTICS: tuple[str, ...] = (
    "result_without_largest_cluster",
    "result_without_top_symbol",
)

# Die Pflichtoffenlegung. ``n_valid`` ist ausdruecklich NICHT die Zahl der
# Feuerungen — eine Feuerung ohne auswertbares Label zaehlt nicht mit, sonst
# waere ``n_valid_min`` eine andere Groesse als die, die sie zu sein vorgibt.
MANDATORY_DISCLOSURE: tuple[str, ...] = (
    "raw_fires",
    "label_capable_fires",
    "n_valid",
    "data_unavailable_count",
    "n_clusters",
    "symbols_with_valid_signals",
    "per_symbol_signals",
    "top_symbol_share",
    "top_cluster_share",
    "leave_one_out_top_symbol",
)

DATA_UNAVAILABLE_POLICY: dict[str, str] = {
    "past_valid_observations": "retained",
    "asset_substitution": "forbidden",
    "zero_filling": "forbidden",
    "unavailable_as_no_signal": "forbidden — Nichtbeobachtbarkeit ist kein Nullsignal",
    "fire_without_label": "NOT_VALID — zaehlt nicht zu n_valid",
    "disclosure": "mandatory",
    "canonical_universe_membership": "immutable — kein Asset rueckt jemals nach",
    "canonical_rename_continuity": (
        "Das Provider-Mapping darf NUR geaendert werden, wenn der bisherige "
        "Provider-Ticker nicht mehr TRADING ist UND ein offiziell dokumentierter "
        "1:1-Nachfolger bzw. eine reine Redenominierung DESSELBEN kanonischen "
        "Assets existiert. Die Aenderung darf Universe-Mitgliedschaft weder "
        "hinzufuegen noch entfernen noch doppelt gewichten und muss zeitgestempelt "
        "und auditierbar dokumentiert werden. Keine heuristische Alias-Suche. "
        "Keine Entscheidung nach Performance."
    ),
    "true_delisting_without_successor": (
        "Alle bis zum Delisting erzeugten gueltigen Signale bleiben enthalten; "
        "ab Delisting keine neuen Signale; keine Substitution; keine "
        "Neugewichtung; Delisting-Zeitpunkt wird offengelegt."
    ),
}


@dataclass(frozen=True)
class PreRegCandidate:
    """Alle Forschungsparameter — vollstaendig, aber ohne Uhr.

    ``t0_utc`` fehlt hier bewusst als Feld: was nicht existiert, kann nicht
    stillschweigend nachgetragen werden.
    """

    prereg_version: str
    hypothesis: str
    universe_sha256: str
    n_symbols: int
    timeframe: str
    horizon: int
    n_valid_min: int
    cluster_min: int
    t1_offset_days: int
    t2_offset_days: int
    alpha: float
    round_trip_cost_bps: float
    economic_floor_bps: float
    primary_estimand: str
    inference: str
    per_symbol_status: str
    execution_convention: str
    status: str = STATUS_CANDIDATE
    spec_version: str = PREREG_SPEC_VERSION
    sensitivity_cost_bps: tuple[float, ...] = SENSITIVITY_COST_BPS
    robustness_diagnostics: tuple[str, ...] = ROBUSTNESS_DIAGNOSTICS
    mandatory_disclosure: tuple[str, ...] = MANDATORY_DISCLOSURE
    data_unavailable_policy: dict[str, str] = field(
        default_factory=lambda: dict(DATA_UNAVAILABLE_POLICY)
    )


@dataclass(frozen=True)
class PreRegActivation:
    """Startet die Uhr. Aendert keinen einzigen Forschungsparameter."""

    candidate_sha256: str
    universe_sha256: str
    research_code_sha: str
    evaluator_sha256: str
    t0_utc: str
    t1_utc: str
    t2_utc: str
    operator_approved: bool
    status: str = STATUS_ACTIVE
    spec_version: str = PREREG_SPEC_VERSION


def _canonical_json(payload: dict[str, Any]) -> str:
    """Stabil serialisieren: sortiert, ohne Leerzeichen, mit Versionspraefix.

    Der Praefix sorgt dafuer, dass eine geaenderte Serialisierung einen anderen
    Hash ergibt, statt still denselben zu reproduzieren.
    """
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"{PREREG_SPEC_VERSION}\n{body}\n"


def candidate_to_dict(candidate: PreRegCandidate) -> dict[str, Any]:
    payload = asdict(candidate)
    payload["sensitivity_cost_bps"] = list(candidate.sensitivity_cost_bps)
    payload["robustness_diagnostics"] = list(candidate.robustness_diagnostics)
    payload["mandatory_disclosure"] = list(candidate.mandatory_disclosure)
    return payload


def candidate_sha256(candidate: PreRegCandidate) -> str:
    """Hash ueber ALLE Parameter. Eine geaenderte Zahl = ein anderer Candidate."""
    return hashlib.sha256(_canonical_json(candidate_to_dict(candidate)).encode("utf-8")).hexdigest()


def activation_to_dict(activation: PreRegActivation) -> dict[str, Any]:
    return asdict(activation)


def activation_sha256(activation: PreRegActivation) -> str:
    return hashlib.sha256(
        _canonical_json(activation_to_dict(activation)).encode("utf-8")
    ).hexdigest()


def activate(
    candidate: PreRegCandidate,
    *,
    t0_utc: str,
    research_code_sha: str,
    evaluator_sha256: str,
    operator_approved: bool,
) -> PreRegActivation:
    """Erzeuge das Activation-Record. T1/T2 folgen aus den Offsets, nicht aus Eingabe.

    Args:
        candidate: der geschlossene Candidate. Wird NICHT veraendert.
        t0_utc: Startzeitpunkt der OOS-Uhr (ISO-8601, UTC).
        research_code_sha: Commit-SHA des Codes, der die Features erzeugt.
        evaluator_sha256: Hash des Evaluators, der das Verdikt faellt.
        operator_approved: muss True sein — eine Praeregistrierung aktiviert sich
            nicht selbst.

    Raises:
        ValueError: Candidate nicht im Candidate-Status, fehlende SHAs,
            fehlende Operator-Freigabe oder unlesbares ``t0_utc``.
    """
    if candidate.status != STATUS_CANDIDATE:
        raise ValueError(f"candidate has status {candidate.status!r}, expected CANDIDATE")
    if not operator_approved:
        raise ValueError("activation requires explicit operator approval")
    if not research_code_sha or not evaluator_sha256:
        raise ValueError("research_code_sha and evaluator_sha256 are mandatory")

    start = datetime.fromisoformat(t0_utc)
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)

    return PreRegActivation(
        candidate_sha256=candidate_sha256(candidate),
        universe_sha256=candidate.universe_sha256,
        research_code_sha=research_code_sha,
        evaluator_sha256=evaluator_sha256,
        t0_utc=start.isoformat(),
        t1_utc=(start + timedelta(days=candidate.t1_offset_days)).isoformat(),
        t2_utc=(start + timedelta(days=candidate.t2_offset_days)).isoformat(),
        operator_approved=True,
    )


def build_rsi_reentry_volume_candidate(
    universe_sha256_value: str,
    n_symbols: int,
) -> PreRegCandidate:
    """Der konkrete Candidate — Operator-Freigabe vom 2026-08-20/21.

    Die Schranken sind nicht gegriffen, sondern aus der Frequenz- und
    Abhaengigkeitsmessung abgeleitet (``dependency_preflight``, 180 d, kanonische
    34): 1,617 gueltige Signale/Tag und 0,833 Cluster/Tag. ``n_valid_min = 100``
    bindet damit an Tag 62, ``cluster_min = 50`` an Tag 60 — beide Schranken
    greifen fast gleichzeitig, keine ist Dekoration. Bei T1 = 90 Tagen bleibt
    rund die Haelfte als Reserve, T2 = 180 Tage deckt eine halbierte Feuerrate.
    """
    return PreRegCandidate(
        prereg_version="rsi_reentry_volume_v1",
        hypothesis="rsi_reentry_volume_confirmed",
        universe_sha256=universe_sha256_value,
        n_symbols=n_symbols,
        timeframe="1h",
        horizon=4,
        n_valid_min=100,
        cluster_min=50,
        t1_offset_days=90,
        t2_offset_days=180,
        alpha=0.05,
        round_trip_cost_bps=PREREG_ROUND_TRIP_COST_BPS,
        economic_floor_bps=PREREG_ECONOMIC_FLOOR_BPS,
        primary_estimand="mean net bps per valid signal, pooled across the sealed universe",
        inference="CR1 cluster-robust Student-t, df = G-1, one-sided H0: mean <= 0",
        per_symbol_status="DIAGNOSTIC_NON_GATING",
        execution_convention="signal close(t) -> entry open(t+1) -> exit close(t+h)",
    )


def _write_candidate() -> int:  # pragma: no cover - Einstiegspunkt
    """Schreibt das Candidate-Artefakt aus dem versiegelten Universum.

    Zweistufig gedacht: dieses Artefakt ist unveraenderlich. Wer spaeter T0
    setzt, erzeugt ein zweites Artefakt (Activation) und editiert dieses hier
    NICHT.
    """
    import argparse

    parser = argparse.ArgumentParser(description="Praeregistrierungs-Candidate schliessen")
    parser.add_argument("universe_json")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    universe = json.loads(Path(args.universe_json).read_text(encoding="utf-8"))
    if not universe.get("ok"):
        print("FATAL: Universum ist nicht versiegelbar (ok=false)")
        return 1

    candidate = build_rsi_reentry_volume_candidate(
        universe["universe_sha256"], universe["n_symbols"]
    )
    payload = candidate_to_dict(candidate)
    payload["candidate_sha256"] = candidate_sha256(candidate)
    payload["t0_utc"] = "NOT_SET"
    payload["activation_preconditions"] = [
        "Pi privilege broker installed and verified",
        "systemd unit drift resolved",
        "timer postconditions proven",
        "deploy verdict no longer HOLD",
    ]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"PREREG_CANDIDATE_SHA256 = {payload['candidate_sha256']}")
    print(f"UNIVERSE_SHA256         = {candidate.universe_sha256}")
    print(f"status                  = {candidate.status}")
    print("T0                      = NOT_SET")
    print(f"geschrieben: {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_write_candidate())
