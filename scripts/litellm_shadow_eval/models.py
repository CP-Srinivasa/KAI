"""Typed, I/O-free contracts for offline shadow evidence."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Literal

#: Ein Ausschlussgrund ist ein Code, kein Freitext: er landet gezaehlt im
#: Bericht, und ein Bericht traegt keine Prompts und keine Nutzlast.
_EXCLUSION_CODE = re.compile(r"[A-Za-z0-9_.:-]{1,64}")


def is_exclusion_code(value: object) -> bool:
    return isinstance(value, str) and _EXCLUSION_CODE.fullmatch(value) is not None


class Side(StrEnum):
    DIRECT = "DIRECT"
    SHADOW = "SHADOW"


class PairStatus(StrEnum):
    VALID = "VALID_PAIR"
    INCOMPLETE = "INCOMPLETE_PAIR"


class GraduationStatus(StrEnum):
    READY = "READY"
    NOT_READY = "NOT_READY"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    INVALID_EVIDENCE = "INVALID_EVIDENCE"


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    message: str
    record_ref: str
    logical_route: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    schema_version: str
    evaluation_id: str | None
    correlation_id: str | None
    call_id: str | None
    logical_route: str
    purpose: str
    side: Side
    mode: str | None
    requested_alias: str | None
    actual_provider: str | None
    actual_model: str | None
    identity_proven: bool
    success: bool
    schema_valid: bool | None
    outcome: str | None
    error_class: str | None
    fallback_used: bool | None
    retry_count: int | None
    attempt_count: int | None
    latency_ms: float | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    cost_known: bool
    response_fingerprint: str | None
    quality_score: float | None
    timestamp: str
    execution_authority: bool | None
    record_ref: str
    #: Fehlerklassen ALLER physischen Versuche dieser einen logischen Seite, in
    #: Versuchsreihenfolge. Ein Retry ist kein Detail, das man wegmittelt: wenn
    #: drei Versuche als `timeout, timeout, ok` enden, ist `ok` das Ergebnis,
    #: aber die zwei Timeouts sind die Beobachtung. Leer heisst: ein Versuch.
    attempt_error_classes: tuple[str, ...] = ()
    #: Warum dieser Seite das Gegenstueck fehlt -- ein Code, kein Freitext.
    #: Wirkt NUR bei unvollstaendigen Paaren und nur, wenn die Politik den
    #: Code vorab zugelassen hat. Ein vollstaendiges Paar entfernt er nie aus
    #: der Population: sonst liesse sich jeder Schattenausfall wegerklaeren.
    exclusion_reason: str | None = None

    @property
    def pair_key(self) -> str | None:
        if self.evaluation_id:
            return f"evaluation:{self.evaluation_id}"
        if self.correlation_id and self.call_id and self.logical_route:
            return f"call:{self.correlation_id}:{self.call_id}:{self.logical_route}"
        return None


@dataclass(frozen=True, slots=True)
class EvidencePair:
    key: str
    logical_route: str
    direct: EvidenceRecord | None
    shadow: EvidenceRecord | None
    status: PairStatus


@dataclass(frozen=True, slots=True)
class QualityComparison:
    status: Literal["MEASURED", "NOT_MEASURED"]
    sample_count: int
    direct_mean: float | None
    shadow_mean: float | None
    delta_mean: float | None
    delta_median: float | None
    shadow_better_count: int
    direct_better_count: int
    equal_count: int
    #: Anteil der vollstaendigen Paare, die in der Stichprobe stecken. `None`
    #: nur ohne vollstaendige Paare; 0.0 heisst "keines bewertet".
    coverage: float | None = None
    #: sha256 ueber die sortierten Paarschluessel der Stichprobe, je Schluessel
    #: eine Zeile mit abschliessendem ``\n`` (UTF-8). Macht die AUSWAHL
    #: nachpruefbar: wer dieselbe Stichprobe behauptet, muss denselben Hash
    #: vorzeigen. `None`, wenn nichts bewertet wurde.
    sample_keys_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class RouteMetrics:
    logical_route: str
    sample_count: int
    complete_pair_count: int
    incomplete_pair_count: int
    invalid_record_count: int
    direct_success_rate: float | None
    shadow_success_rate: float | None
    direct_schema_valid_rate: float | None
    shadow_schema_valid_rate: float | None
    shadow_fallback_rate: float | None
    shadow_retry_rate: float | None
    direct_p50_latency_ms: float | None
    direct_p95_latency_ms: float | None
    shadow_p50_latency_ms: float | None
    shadow_p95_latency_ms: float | None
    latency_delta_p50_ms: float | None
    latency_delta_p95_ms: float | None
    latency_ratio_p50: float | None
    latency_ratio_p95: float | None
    provider_identity_known_rate: float | None
    model_identity_known_rate: float | None
    cost_known_rate: float | None
    direct_mean_cost_usd: float | None
    direct_median_cost_usd: float | None
    shadow_mean_cost_usd: float | None
    shadow_median_cost_usd: float | None
    known_cost_delta_mean: float | None
    known_cost_delta_median: float | None
    unknown_cost_count: int
    unknown_identity_count: int
    model_substitution_rate: float | None
    provider_substitution_rate: float | None
    error_distribution_direct: dict[str, int]
    error_distribution_shadow: dict[str, int]
    attempt_error_distribution_shadow: dict[str, int]
    outcome_distribution: dict[str, int]
    retry_distribution: dict[str, int]
    fallback_distribution: dict[str, int]
    schema_divergence_rate: float | None
    success_divergence_rate: float | None
    response_divergence_rate: float | None
    #: Unvollstaendige Paare OHNE vorregistrierten Ausschlussgrund und ihr
    #: Anteil an allen Paaren (vollstaendig + unvollstaendig).
    unexplained_incomplete_pair_count: int
    unexplained_incomplete_rate: float | None
    #: Ausschlussgruende der unvollstaendigen Paare, wie geliefert -- auch die
    #: nicht zugelassenen. ``(none)`` = kein Grund angegeben.
    exclusion_reason_distribution: dict[str, int]
    #: SHADOW-Seiten ohne bekannten `retry_count` ODER `attempt_count`.
    unknown_attempt_accounting_count: int
    quality: QualityComparison

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        # Ohne Messung sind die Zahlen `null`, nicht 0. Eine 0 waere eine
        # Aussage ueber die Qualitaet; `null` ist die Aussage, dass keine
        # vorliegt. Wer das verwechselt, liest spaeter einen Gleichstand.
        value.update(
            {
                "quality_status": self.quality.status,
                "quality_sample_count": self.quality.sample_count,
                "quality_direct_mean": self.quality.direct_mean,
                "quality_shadow_mean": self.quality.shadow_mean,
                "quality_delta_mean": self.quality.delta_mean,
                "quality_delta_median": self.quality.delta_median,
                "quality_coverage": self.quality.coverage,
                "quality_sample_keys_sha256": self.quality.sample_keys_sha256,
                "shadow_better_count": self.quality.shadow_better_count,
                "direct_better_count": self.quality.direct_better_count,
                "equal_count": self.quality.equal_count,
            }
        )
        return value


#: Betriebsnachweise, die als datiertes, referenziertes Artefakt vorliegen
#: koennen. Die beiden ``*_gate_changed``-Schalter gehoeren NICHT dazu: sie sind
#: Zustandsaussagen, keine Nachweise, und bleiben Booleans.
RUNTIME_PROOF_FLAGS: tuple[str, ...] = (
    "off_mode_proven",
    "rollback_proven",
    "gateway_down_proven",
    "timeout_retry_proven",
    "rate_limit_retry_proven",
    "auth_no_retry_proven",
    "server_error_retry_proven",
    "circuit_proven",
    "direct_fallback_proven",
)
RUNTIME_GATE_FLAGS: tuple[str, ...] = ("trading_gate_changed", "execution_gate_changed")


@dataclass(frozen=True, slots=True)
class RuntimeProof:
    """Worauf sich ein ``*_proven`` stuetzt: Artefakt, Hash, Zeitpunkt, Stand.

    Ein nacktes ``true`` sagt nur, DASS jemand etwas behauptet. Eine Freigabe
    muss auf etwas zeigen koennen, das man nachlesen und datieren kann.
    ``proven_at`` ist ISO-8601 in UTC (vom Parser normalisiert).
    """

    proven: bool
    artifact: str | None = None
    artifact_sha256: str | None = None
    proven_at: str | None = None
    version: str | None = None

    @property
    def referenced(self) -> bool:
        return self.proven and all(
            (self.artifact, self.artifact_sha256, self.proven_at, self.version)
        )


@dataclass(frozen=True, slots=True)
class RuntimeEvidenceFlags:
    off_mode_proven: bool = False
    rollback_proven: bool = False
    gateway_down_proven: bool = False
    timeout_retry_proven: bool = False
    rate_limit_retry_proven: bool = False
    auth_no_retry_proven: bool = False
    server_error_retry_proven: bool = False
    circuit_proven: bool = False
    direct_fallback_proven: bool = False
    trading_gate_changed: bool = False
    execution_gate_changed: bool = False
    #: Belegobjekte je ``*_proven``-Flag, soweit als Objekt geliefert. Ein Flag
    #: ohne Eintrag hier kam als nacktes Boolean -- und gilt unter
    #: ``require_referenced_runtime_evidence`` als NICHT belegt.
    proofs: dict[str, RuntimeProof] = field(default_factory=dict)

    def proof(self, flag: str) -> RuntimeProof | None:
        return self.proofs.get(flag)

    def to_dict(self) -> dict[str, Any]:
        """Berichtsform: jeder Nachweis als Objekt, auch der unbelegte."""
        value: dict[str, Any] = {}
        for name in RUNTIME_PROOF_FLAGS:
            proven = bool(getattr(self, name))
            proof = self.proofs.get(name)
            value[name] = {
                "proven": proven,
                "referenced": bool(proven and proof is not None and proof.referenced),
                "artifact": proof.artifact if proof else None,
                "artifact_sha256": proof.artifact_sha256 if proof else None,
                "proven_at": proof.proven_at if proof else None,
                "version": proof.version if proof else None,
            }
        for name in RUNTIME_GATE_FLAGS:
            value[name] = bool(getattr(self, name))
        return value


@dataclass(frozen=True, slots=True)
class GraduationPolicy:
    minimum_sample_count: int = 100
    minimum_success_rate: float = 0.99
    minimum_schema_valid_rate: float = 0.99
    maximum_unbounded_retry: bool = False
    require_off_mode_proven: bool = True
    require_rollback_proven: bool = True
    require_gateway_down_fallback_proven: bool = True
    require_auth_no_retry_proven: bool = True
    require_execution_gate_unchanged: bool = True
    require_trading_gate_unchanged: bool = True
    require_identity_observability: bool = True
    #: Fehlende Qualitaetsbelege duerfen nicht automatisch zu READY fuehren.
    #: Wer Qualitaet bewusst als beratend behandeln will, muss das HIER
    #: hinschreiben -- dann steht es im Policy-Hash und ist nachweisbar eine
    #: Entscheidung gewesen, kein Versehen. Beratend betrifft FEHLENDE oder zu
    #: duenne Belege; eine gemessene Regression blockiert immer.
    require_quality_evidence: bool = True
    #: Mindestzahl qualitaetsbewerteter Paare. `None` = dieselbe Zusage wie
    #: `minimum_sample_count` (auch nach einer Routenausnahme). Ein einziges
    #: bewertetes Paar ist keine Qualitaetsmessung, sondern eine Anekdote.
    minimum_quality_sample_count: int | None = None
    #: Mindestanteil der vollstaendigen Paare, der bewertet sein muss. 1.0
    #: heisst: jedes Paar, auch das gescheiterte (ein Ausfall ist mit 0.0 zu
    #: bewerten, nicht wegzulassen). Eine Teilstichprobe ist eine Auswahl, und
    #: eine Auswahl kann man sich aussuchen -- deshalb nur ausdruecklich.
    minimum_quality_coverage: float = 1.0
    #: Wie weit das SHADOW-Mittel hoechstens unter dem DIRECT-Mittel liegen darf.
    maximum_quality_regression: float = 0.02
    #: Jede SHADOW-Seite braucht bekannte `retry_count` UND `attempt_count`.
    #: Sonst verschwindet "UNKNOWN" still aus der Retry-Grenze.
    require_complete_attempt_accounting: bool = True
    #: Jede SHADOW-Seite braucht bekannte Kosten (`cost_known_rate == 1.0`).
    require_cost_known: bool = True
    #: Hoechstanteil unvollstaendiger Paare ohne zugelassenen Ausschlussgrund,
    #: gemessen an allen Paaren. 0.0: jedes halbe Paar muss erklaert sein.
    maximum_unexplained_incomplete_rate: float = 0.0
    #: Vorab zugelassene Ausschlussgruende (Codes). Leer: keiner. Ein Grund
    #: wie ``circuit_open`` ist ein Schattenausfall, kein harmloser Ausschluss
    #: -- deshalb erklaert nicht jeder geschriebene Grund, sondern nur ein
    #: hier vorregistrierter.
    allowed_exclusion_reasons: tuple[str, ...] = ()
    require_timeout_retry_proven: bool = True
    require_rate_limit_retry_proven: bool = True
    require_server_error_retry_proven: bool = True
    require_circuit_proven: bool = True
    #: Verlangte Betriebsnachweise muessen als Objekt mit Artefakt, sha256,
    #: Zeitpunkt und Stand vorliegen. Ein nacktes `true` gilt nicht.
    require_referenced_runtime_evidence: bool = True
    #: Hoechstalter eines referenzierten Nachweises gegen `generated_at`.
    #: `None` schaltet die Altersgrenze ab.
    maximum_runtime_proof_age_days: int | None = 30
    route_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GraduationDecision:
    logical_route: str
    status: GraduationStatus
    reasons: tuple[str, ...]
    shadow_validated: bool
    consensus_route: bool = False

    @property
    def primary_ready(self) -> bool:
        """Darf diese Route ueberhaupt fuer PRIMARY vorgeschlagen werden?

        Getrennt von :attr:`status`, weil ``READY`` eine Aussage ueber die
        BELEGLAGE ist und ``primary_ready`` eine ueber die Erlaubnis. Fuer die
        Consensus-Route fallen die beiden auseinander: sie kann vollstaendig
        belegt sein und trotzdem nie PRIMARY werden. Wer nur ``status`` liest,
        wuerde genau diesen Unterschied uebersehen.
        """
        return self.status is GraduationStatus.READY and not self.consensus_route

    def to_dict(self) -> dict[str, Any]:
        return {
            "logical_route": self.logical_route,
            "status": self.status.value,
            "reasons": list(self.reasons),
            "shadow_validated": self.shadow_validated,
            "consensus_route": self.consensus_route,
            "primary_ready": self.primary_ready,
            # Invariante, keine Konfiguration: es gibt keinen Wert, keine
            # Stichprobengroesse und keine Erfolgsquote, die das auf true dreht.
            "consensus_primary_allowed": False,
        }


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    tool_version: str
    policy_hash: str
    input_sha256: str
    input_files: dict[str, str]
    record_count: int
    generated_at: str
    routes: tuple[str, ...]
    invalid_record_count: int
    validation_issues: tuple[ValidationIssue, ...]
    metrics: dict[str, RouteMetrics]
    decisions: dict[str, GraduationDecision]
    runtime_evidence: RuntimeEvidenceFlags

    def to_dict(self) -> dict[str, Any]:
        return {
            # v2: Laufzeitnachweise mit Artefaktbezug, Stichprobenhash,
            # unerklaerte halbe Paare und Versuchszaehlung sind Pflichtfelder.
            "schema_version": "litellm-shadow-eval-report/v2",
            "tool_version": self.tool_version,
            "policy_hash": self.policy_hash,
            "input_sha256": self.input_sha256,
            "input_files": dict(sorted(self.input_files.items())),
            "record_count": self.record_count,
            "generated_at": self.generated_at,
            "routes": list(self.routes),
            "invalid_record_count": self.invalid_record_count,
            "validation_issues": [item.to_dict() for item in self.validation_issues],
            "metrics": {key: value.to_dict() for key, value in sorted(self.metrics.items())},
            "decisions": {key: value.to_dict() for key, value in sorted(self.decisions.items())},
            # Worauf sich die Betriebsnachweise stuetzen: eine Freigabe soll auf
            # datierte Artefakte zeigen, nicht auf ein `true` in einer Datei.
            "runtime_evidence": self.runtime_evidence.to_dict(),
            # Die maschinenlesbare Wahrheit ueber Reife. Der Exit-Code des CLI
            # sagt, ob der Auswerter durchgelaufen ist -- nicht, ob PRIMARY
            # erlaubt waere. Diese Liste sagt es.
            "primary_ready_routes": sorted(
                key for key, value in self.decisions.items() if value.primary_ready
            ),
        }


__all__ = [
    "EvaluationReport",
    "EvidencePair",
    "EvidenceRecord",
    "GraduationDecision",
    "GraduationPolicy",
    "GraduationStatus",
    "PairStatus",
    "QualityComparison",
    "RUNTIME_GATE_FLAGS",
    "RUNTIME_PROOF_FLAGS",
    "RouteMetrics",
    "RuntimeEvidenceFlags",
    "RuntimeProof",
    "Side",
    "ValidationIssue",
    "is_exclusion_code",
]
