"""Typisierte, I/O-freie Bausteine des Routen-Abnahmeberichts.

**Unbekannt ist nicht 0.** Jedes Feld, das die Eingaben nicht belegen, steht als
``None`` im Bericht -- und daneben, in ``null_reasons``, warum. Eine Null waere
eine Messung; ``None`` ist die Aussage, dass keine vorliegt.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Final

#: Spiegel von ``app.ai.routes.ROUTES``. Das Paket importiert ``app`` nicht
#: (dieselbe Grenze wie ``scripts/litellm_shadow_eval``); ein Vertragstest haelt
#: beide Seiten gleich. Eine Katalogroute ohne Aufruf erscheint im Bericht als
#: ``KEINE_EVIDENZ`` -- sie wird gefuehrt, nicht weggelassen.
CATALOGUE_ROUTES: Final[tuple[str, ...]] = (
    "bulk",
    "standard",
    "reasoning",
    "critical",
    "stt",
    "research",
)

#: Spiegel von ``app.ai.routes.route_for``. Der Direktpfad im OFF-Modus
#: schreibt keine ``logical_route`` in die Zeile, obwohl die Runtime sie vorher
#: genau so aus dem Zweck bestimmt hat. Wo der Bericht sie deshalb ableitet,
#: zaehlt er es sichtbar mit (``route_from_purpose``).
PURPOSE_ROUTE: Final[dict[str, str]] = {
    "analysis": "standard",
    "chat": "standard",
    "intent": "critical",
    "stt": "stt",
    "consensus": "reasoning",
    "research": "research",
}


class RouteStatus(StrEnum):
    BELEGT = "BELEGT"
    LUECKENHAFT = "LUECKENHAFT"
    KEINE_EVIDENZ = "KEINE_EVIDENZ"


@dataclass(frozen=True, slots=True)
class CallRecord:
    """Eine Telemetriezeile, reduziert auf das, was der Bericht auswertet."""

    ts: datetime
    route: str | None
    route_from_purpose: bool
    transport: str
    mode: str | None
    ok: bool
    #: ``ok`` UND ``outcome`` ist ``success``. Ein abgeschnittener Aufruf ist
    #: technisch ``ok`` und liefert trotzdem kein Ergebnis.
    success: bool
    truncated: bool
    correlation_id: str | None
    chain_position: int
    identity_proven: bool
    #: ``anbieter/modell`` aus der Antwort -- nur bei ``identity_proven``.
    actual: str | None
    #: Was konfiguriert oder angefordert war. Nie als Identitaet gefuehrt.
    claimed: str | None
    error_class: str | None
    retry_count: int | None
    transport_retries: int | None
    fallback_known: bool
    fallback_used: bool
    circuit_state: str | None
    cost_usd: float | None
    cost_source: str | None
    usage_reported: bool
    #: Das Release, aus dem die Zeile stammt (Telemetrie v9). ``None`` bei
    #: aelteren Zeilen oder einem Wert, der kein 40-stelliger Commit ist.
    runtime_commit: str | None = None


@dataclass(frozen=True, slots=True)
class VersionInfo:
    transport_version: str | None
    transport_manifest: str | None
    transport_tree: str | None
    release_sha: str | None
    source: str | None
    #: Woher ``release_sha`` stammt, samt Wechseln und Luecken im Fenster.
    release_source: str | None = None


@dataclass(frozen=True, slots=True)
class IdentityInfo:
    proven: int
    #: Anteil ``identity_proven`` an allen Aufrufen.
    share: float | None
    successes: int
    successes_unproven: int
    actual: dict[str, int]
    unproven_claimed: dict[str, int]


@dataclass(frozen=True, slots=True)
class CostInfo:
    known: int
    unknown: int
    #: Fehlversuch ohne gemeldeten Verbrauch: kein unbekannter Kostenfall (D-271).
    failed_without_usage: int
    sum_known_usd: float | None
    sum_complete: bool | None
    median_usd: float | None
    sources: dict[str, int]


@dataclass(frozen=True, slots=True)
class FailureInfo:
    failures: int
    error_classes: dict[str, int]
    retry_rows: int
    max_retry_count: int | None
    retry_unknown: int
    transport_retries_total: int | None
    fallbacks: int
    fallback_rate: float | None
    fallback_unknown: int
    circuit_states: dict[str, int] | None
    circuit_unknown: int


@dataclass(frozen=True, slots=True)
class EvidenceAge:
    newest_at: str | None
    oldest_at: str | None
    newest_age_hours: float | None
    oldest_age_hours: float | None
    stale: bool | None


@dataclass(frozen=True, slots=True)
class TransportEvidence:
    """Eine logische Route auf EINEM Transport (``litellm``, ``direct``, ...)."""

    transport: str
    status: RouteStatus
    missing: tuple[str, ...]
    calls: int
    route_from_purpose: int
    modes: dict[str, int]
    version: VersionInfo
    last_success_at: str | None
    identity: IdentityInfo
    cost: CostInfo
    failure: FailureInfo
    evidence_age: EvidenceAge
    null_reasons: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        value["missing"] = list(self.missing)
        return value


@dataclass(frozen=True, slots=True)
class RouteReport:
    logical_route: str
    in_catalogue: bool
    status: RouteStatus
    #: ``<transport>:<feld>`` -- nur bei ``LUECKENHAFT`` nicht leer.
    missing: tuple[str, ...]
    transports: dict[str, TransportEvidence]
    #: Aus dem Eval-Report UEBERNOMMEN, nicht neu bewertet; geht nicht in
    #: ``status`` ein.
    eval_decision: dict[str, Any] | None
    null_reasons: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "logical_route": self.logical_route,
            "in_catalogue": self.in_catalogue,
            "status": self.status.value,
            "missing": list(self.missing),
            "transports": {name: item.to_dict() for name, item in self.transports.items()},
            "eval_decision": self.eval_decision,
            "null_reasons": dict(self.null_reasons),
        }


@dataclass(frozen=True, slots=True)
class TransportVerification:
    """Eine ``TRANSPORT_VERIFIED``-Zeile aus ``scripts/pi_transport_exec.sh``."""

    name: str
    version: str | None
    tree: str | None
    manifest: str | None
    verified_at: str | None
    null_reasons: dict[str, str]


@dataclass(frozen=True, slots=True)
class TransportLogSummary:
    label: str
    sha256: str
    verified_lines: int
    #: Je Transportname die LETZTE Zeile -- die, die den laufenden Baum nennt.
    last: dict[str, TransportVerification]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class EvalReportSummary:
    label: str
    sha256: str
    schema_version: str
    tool_version: str | None
    generated_at: str | None
    age_hours: float | None
    policy_hash: str | None
    input_sha256: str | None
    primary_ready_routes: list[str] | None
    decisions: dict[str, dict[str, Any]]
    runtime_evidence: dict[str, Any] | None
    null_reasons: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TelemetrySummary:
    files: dict[str, str]
    rows_read: int
    rows_valid: int
    outer_chain_rows_dropped: int
    unattributed_rows: int
    rows_used: int
    issues: dict[str, int]
    schema_versions: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RouteAcceptanceReport:
    tool_version: str
    generated_at: str
    max_evidence_age_hours: float
    telemetry: TelemetrySummary
    eval_report: EvalReportSummary | None
    transport_log: TransportLogSummary | None
    routes: dict[str, RouteReport]
    null_reasons: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        counts = {status.value: 0 for status in RouteStatus}
        for route in self.routes.values():
            counts[route.status.value] += 1
        return {
            "schema_version": "litellm-route-report/v1",
            "tool_version": self.tool_version,
            "generated_at": self.generated_at,
            "max_evidence_age_hours": self.max_evidence_age_hours,
            # Invariante, keine Einstellung: der Bericht zeigt Belege. Er gibt
            # nichts frei und schaltet nichts um.
            "advisory_only": True,
            "inputs": {
                "telemetry": self.telemetry.to_dict(),
                "eval_report": self.eval_report.to_dict() if self.eval_report else None,
                "transport_log": self.transport_log.to_dict() if self.transport_log else None,
            },
            "routes": {name: route.to_dict() for name, route in sorted(self.routes.items())},
            "status_counts": counts,
            "null_reasons": dict(sorted(self.null_reasons.items())),
        }


__all__ = [
    "CATALOGUE_ROUTES",
    "PURPOSE_ROUTE",
    "CallRecord",
    "CostInfo",
    "EvalReportSummary",
    "EvidenceAge",
    "FailureInfo",
    "IdentityInfo",
    "RouteAcceptanceReport",
    "RouteReport",
    "RouteStatus",
    "TelemetrySummary",
    "TransportEvidence",
    "TransportLogSummary",
    "TransportVerification",
    "VersionInfo",
]
