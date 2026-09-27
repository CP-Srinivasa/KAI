"""Aus den Eingaben den Routen-Abnahmebericht bauen -- rein bis auf das Lesen.

Je logischer Route und je Transport (``litellm`` und ``direct`` stehen immer
da) sechs Felder: Version, letzter Erfolg, Identitaet, Kosten,
Ausfallverhalten, Evidenzalter. Der Zustand ist dreiwertig:

``BELEGT``         jeder genutzte Transport der Route belegt alle Pflichtfelder.
``LUECKENHAFT``    mindestens ein Pflichtfeld fehlt; ``missing`` nennt es.
``KEINE_EVIDENZ``  keine einzige Telemetriezeile fuer diese Route.

Pflicht fuer einen Transport mit Aufrufen: ein Erfolg; jeder Erfolg mit
belegter Identitaet; jede Kostenangabe bekannt; Retry- und Fallback-Angabe in
jeder Zeile; auf ``litellm`` zusaetzlich Circuit-Zustand und Transportversion;
juengster Beleg nicht aelter als ``max_age_hours``. Der Release-SHA ist KEIN
Pflichtfeld: keine Eingabe traegt ihn je Route, er steht als ``null`` mit Grund
da. Ein Pflichtfeld, das keine Eingabe je belegen kann, machte ``BELEGT``
unerreichbar -- und einen Zustand, den es nie gibt, liest niemand mehr.

Der Bericht trifft keine Freigabe und aktiviert nichts. Den Eval-Report
uebernimmt er, er bewertet ihn nicht neu.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Final

from scripts.litellm_route_report.inputs import (
    KNOWN_TELEMETRY_SCHEMAS,
    InputError,
    load_eval_report,
    load_telemetry,
    load_transport_log,
)
from scripts.litellm_route_report.models import (
    CATALOGUE_ROUTES,
    PURPOSE_ROUTE,
    CallRecord,
    CostInfo,
    EvalReportSummary,
    EvidenceAge,
    FailureInfo,
    IdentityInfo,
    RouteAcceptanceReport,
    RouteReport,
    RouteStatus,
    TelemetrySummary,
    TransportEvidence,
    TransportLogSummary,
    VersionInfo,
)

TOOL_VERSION: Final = "1.0.0"
DEFAULT_MAX_AGE_HOURS: Final = 168.0

#: Diese beiden stehen bei jeder Route im Bericht, auch ohne Aufruf -- genau
#: der Vergleich zeigt, ueber welchen Weg die Produktion tatsaechlich laeuft.
FIXED_TRANSPORTS: Final = ("litellm", "direct")
#: Nur das LiteLLM-Gateway fuehrt ein Circuit-Buch (``app.ai.gateway``).
CIRCUIT_TRANSPORTS: Final = frozenset({"litellm"})
#: Telemetrie-Transport -> Name in der ``TRANSPORT_VERIFIED``-Zeile.
VERSIONED_TRANSPORTS: Final = {"litellm": "litellm"}

_NO_ROWS: Final = "keine Telemetriezeile fuer diese Route auf diesem Transport"
_NO_RELEASE: Final = (
    "keine Eingabe traegt ihn je Route (Telemetriezeilen fuehren kein Release-Feld)"
)


def _stable(value: float) -> float:
    return round(value, 9)


def _hours(later: datetime, earlier: datetime) -> float:
    return round((later - earlier).total_seconds() / 3600.0, 2)


def _counts(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def drop_outer_chain_records(
    records: Sequence[CallRecord],
) -> tuple[list[CallRecord], int]:
    """Genau eine Zeile je physischem Aufruf -- die Hauptregel aus ``app.ai.spend``.

    Wo Versuchszeilen (``chain_position >= 0``) derselben ``correlation_id``
    existieren, entfaellt die aeussere Kettenzeile (``-1``). Sonst zaehlte jede
    Kettenanalyse doppelt, samt ihrer Kosten. Die enge Altpaar-Regel fuer den
    07.-14.09.2026 bildet dieser Bericht NICHT nach; Zeilen aus diesem Fenster
    koennen doppelt zaehlen.
    """
    attempt_ids = {
        record.correlation_id
        for record in records
        if record.chain_position >= 0 and record.correlation_id
    }
    kept = [
        record
        for record in records
        if not (record.chain_position == -1 and record.correlation_id in attempt_ids)
    ]
    return kept, len(records) - len(kept)


def _version(transport: str, log: TransportLogSummary | None) -> tuple[VersionInfo, dict[str, str]]:
    reasons = {"version.release_sha": _NO_RELEASE}
    name = VERSIONED_TRANSPORTS.get(transport)
    if name is None:
        grund = (
            "Direktpfad hat keinen versionierten Transportbeleg"
            if transport == "direct"
            else "Transport unbekannt; kein Transportbeleg zuordenbar"
        )
        for key in ("transport_version", "transport_manifest", "transport_tree", "source"):
            reasons[f"version.{key}"] = grund
        return VersionInfo(None, None, None, None, None), reasons

    verification = log.last.get(name) if log is not None else None
    if verification is None:
        grund = (
            "kein --transport-log angegeben"
            if log is None
            else f"keine TRANSPORT_VERIFIED-Zeile fuer {name} im Transport-Log"
        )
        for key in ("transport_version", "transport_manifest", "transport_tree", "source"):
            reasons[f"version.{key}"] = grund
        return VersionInfo(None, None, None, None, None), reasons

    for key, value in (
        ("transport_version", verification.version),
        ("transport_manifest", verification.manifest),
        ("transport_tree", verification.tree),
    ):
        if value is None:
            reasons[f"version.{key}"] = "letzte TRANSPORT_VERIFIED-Zeile nennt den Wert nicht"
    source = "letzte TRANSPORT_VERIFIED-Zeile des Transport-Logs"
    if verification.verified_at is None:
        # Ohne Zeitstempel ist die Zeile ein Beleg ueber den Baum, nicht ueber
        # den Zeitpunkt, zu dem einzelne Aufrufe liefen.
        source += " (ohne Zeitstempel, nicht an einzelne Aufrufe gebunden)"
    return (
        VersionInfo(
            transport_version=verification.version,
            transport_manifest=verification.manifest,
            transport_tree=verification.tree,
            release_sha=None,
            source=source,
        ),
        reasons,
    )


def _empty_section(
    transport: str, version: VersionInfo, version_reasons: dict[str, str]
) -> TransportEvidence:
    reasons = dict(version_reasons)
    for key in (
        "last_success_at",
        "identity.share",
        "cost.sum_known_usd",
        "cost.sum_complete",
        "cost.median_usd",
        "failure.max_retry_count",
        "failure.transport_retries_total",
        "failure.fallback_rate",
        "failure.circuit_states",
        "evidence_age.newest_at",
        "evidence_age.oldest_at",
        "evidence_age.newest_age_hours",
        "evidence_age.oldest_age_hours",
        "evidence_age.stale",
    ):
        reasons[key] = _NO_ROWS
    return TransportEvidence(
        transport=transport,
        status=RouteStatus.KEINE_EVIDENZ,
        missing=(),
        calls=0,
        route_from_purpose=0,
        modes={},
        version=version,
        last_success_at=None,
        identity=IdentityInfo(0, None, 0, 0, {}, {}),
        cost=CostInfo(0, 0, 0, None, None, None, {}),
        failure=FailureInfo(0, {}, 0, None, 0, None, 0, None, 0, None, 0),
        evidence_age=EvidenceAge(None, None, None, None, None),
        null_reasons=dict(sorted(reasons.items())),
    )


def _section(
    transport: str,
    records: list[CallRecord],
    *,
    now: datetime,
    max_age_hours: float,
    log: TransportLogSummary | None,
) -> TransportEvidence:
    version, version_reasons = _version(transport, log)
    if not records:
        return _empty_section(transport, version, version_reasons)
    reasons = dict(version_reasons)
    calls = len(records)

    successes = [record for record in records if record.success]
    last_success = max((record.ts for record in successes), default=None)
    if last_success is None:
        reasons["last_success_at"] = "kein erfolgreicher Aufruf (ok und outcome=success)"

    proven = sum(record.identity_proven for record in records)
    identity = IdentityInfo(
        proven=proven,
        share=_stable(proven / calls),
        successes=len(successes),
        successes_unproven=sum(not record.identity_proven for record in successes),
        actual=_counts(record.actual for record in records if record.actual),
        unproven_claimed=_counts(
            record.claimed for record in records if not record.identity_proven and record.claimed
        ),
    )

    known = [record.cost_usd for record in records if record.cost_usd is not None]
    failed_without_usage = sum(
        record.cost_usd is None and not record.ok and not record.usage_reported
        for record in records
    )
    unknown = calls - len(known) - failed_without_usage
    if not known:
        reasons["cost.sum_known_usd"] = "keine Zeile mit bekannten Kosten"
        reasons["cost.median_usd"] = "keine Zeile mit bekannten Kosten"
    cost = CostInfo(
        known=len(known),
        unknown=unknown,
        failed_without_usage=failed_without_usage,
        sum_known_usd=_stable(math.fsum(known)) if known else None,
        sum_complete=unknown == 0,
        median_usd=_stable(float(median(known))) if known else None,
        sources=_counts(
            record.cost_source or "ohne_angabe" for record in records if record.cost_usd is not None
        ),
    )

    failed = [record for record in records if not record.success]
    retries = [record.retry_count for record in records if record.retry_count is not None]
    transport_retries = [
        record.transport_retries for record in records if record.transport_retries is not None
    ]
    fallback_rows = [record for record in records if record.fallback_known]
    fallbacks = sum(record.fallback_used for record in fallback_rows)
    circuit = _counts(record.circuit_state for record in records if record.circuit_state)
    if not retries:
        reasons["failure.max_retry_count"] = "keine Zeile traegt retry_count oder attempt"
    if not transport_retries:
        reasons["failure.transport_retries_total"] = "keine Zeile traegt transport_retries"
    if not fallback_rows:
        reasons["failure.fallback_rate"] = "keine Zeile traegt fallback_from/fallback_to"
    if not circuit:
        reasons["failure.circuit_states"] = (
            "keine Zeile traegt circuit_state"
            if transport in CIRCUIT_TRANSPORTS
            else "kein Circuit auf diesem Transport (nur das LiteLLM-Gateway fuehrt einen)"
        )
    failure = FailureInfo(
        failures=len(failed),
        error_classes=_counts(
            record.error_class or ("abgeschnitten" if record.truncated else "unklassifiziert")
            for record in failed
        ),
        retry_rows=sum(count > 0 for count in retries),
        max_retry_count=max(retries) if retries else None,
        retry_unknown=calls - len(retries),
        transport_retries_total=sum(transport_retries) if transport_retries else None,
        fallbacks=fallbacks,
        fallback_rate=_stable(fallbacks / len(fallback_rows)) if fallback_rows else None,
        fallback_unknown=calls - len(fallback_rows),
        circuit_states=circuit or None,
        circuit_unknown=calls - sum(circuit.values()),
    )

    newest = max(record.ts for record in records)
    oldest = min(record.ts for record in records)
    newest_age = _hours(now, newest)
    age = EvidenceAge(
        newest_at=newest.isoformat(),
        oldest_at=oldest.isoformat(),
        newest_age_hours=newest_age,
        oldest_age_hours=_hours(now, oldest),
        stale=newest_age > max_age_hours,
    )

    missing: list[str] = []
    if last_success is None:
        missing.append("letzter_erfolg")
    if proven == 0 or identity.successes_unproven:
        missing.append("identitaet")
    if unknown or not known:
        missing.append("kosten")
    if failure.retry_unknown:
        missing.append("retries")
    if failure.fallback_unknown:
        missing.append("fallback")
    if transport in CIRCUIT_TRANSPORTS and failure.circuit_unknown:
        missing.append("circuit")
    if transport in VERSIONED_TRANSPORTS and version.transport_version is None:
        missing.append("version")
    if age.stale:
        missing.append("evidenzalter")

    return TransportEvidence(
        transport=transport,
        status=RouteStatus.LUECKENHAFT if missing else RouteStatus.BELEGT,
        missing=tuple(missing),
        calls=calls,
        route_from_purpose=sum(record.route_from_purpose for record in records),
        modes=_counts(record.mode or "ohne_modus" for record in records),
        version=version,
        last_success_at=last_success.isoformat() if last_success else None,
        identity=identity,
        cost=cost,
        failure=failure,
        evidence_age=age,
        null_reasons=dict(sorted(reasons.items())),
    )


def _route(
    route: str,
    records: list[CallRecord],
    *,
    now: datetime,
    max_age_hours: float,
    log: TransportLogSummary | None,
    evaluation: EvalReportSummary | None,
) -> RouteReport:
    by_transport: dict[str, list[CallRecord]] = {name: [] for name in FIXED_TRANSPORTS}
    for record in records:
        by_transport.setdefault(record.transport, []).append(record)
    order = [*FIXED_TRANSPORTS, *sorted(set(by_transport) - set(FIXED_TRANSPORTS))]
    sections = {
        name: _section(name, by_transport[name], now=now, max_age_hours=max_age_hours, log=log)
        for name in order
    }
    if not records:
        status, missing = RouteStatus.KEINE_EVIDENZ, ()
    else:
        missing = tuple(
            f"{name}:{field}" for name, section in sections.items() for field in section.missing
        )
        status = RouteStatus.LUECKENHAFT if missing else RouteStatus.BELEGT

    reasons: dict[str, str] = {}
    decision = evaluation.decisions.get(route) if evaluation is not None else None
    if decision is None:
        reasons["eval_decision"] = (
            "kein --eval-report angegeben"
            if evaluation is None
            else "Route kommt im Eval-Report nicht vor"
        )
    return RouteReport(
        logical_route=route,
        in_catalogue=route in CATALOGUE_ROUTES,
        status=status,
        missing=missing,
        transports=sections,
        eval_decision=decision,
        null_reasons=reasons,
    )


def build_report(
    *,
    telemetry: Sequence[Path],
    now: datetime,
    eval_report: Path | None = None,
    transport_log: Path | None = None,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
) -> RouteAcceptanceReport:
    """Den Bericht bauen. Kein Netz, keine Laufzeit, keine Freigabe."""
    if now.tzinfo is None:
        raise InputError("der Berichtszeitpunkt braucht eine Zeitzone")
    if not telemetry:
        raise InputError("mindestens eine Telemetriedatei ist noetig")
    if not math.isfinite(max_age_hours) or max_age_hours <= 0:
        raise InputError("max_age_hours muss eine positive Zahl sein")
    now = now.astimezone(UTC)

    loaded = load_telemetry(list(telemetry))
    kept, dropped = drop_outer_chain_records(loaded.records)
    attributed = [record for record in kept if record.route]
    evaluation = load_eval_report(eval_report, now) if eval_report is not None else None
    log = load_transport_log(transport_log) if transport_log is not None else None

    by_route: dict[str, list[CallRecord]] = {route: [] for route in CATALOGUE_ROUTES}
    for record in attributed:
        assert record.route is not None
        by_route.setdefault(record.route, []).append(record)
    for route in evaluation.decisions if evaluation is not None else ():
        by_route.setdefault(route, [])

    reasons: dict[str, str] = {}
    if evaluation is None:
        reasons["eval_report"] = "kein --eval-report angegeben"
    if log is None:
        reasons["transport_log"] = "kein --transport-log angegeben"
    return RouteAcceptanceReport(
        tool_version=TOOL_VERSION,
        generated_at=now.isoformat(),
        max_evidence_age_hours=float(max_age_hours),
        telemetry=TelemetrySummary(
            files=dict(sorted(loaded.files.items())),
            rows_read=loaded.rows_read,
            rows_valid=len(loaded.records),
            outer_chain_rows_dropped=dropped,
            unattributed_rows=len(kept) - len(attributed),
            rows_used=len(attributed),
            issues=loaded.issues,
            schema_versions=loaded.schema_versions,
        ),
        eval_report=evaluation,
        transport_log=log,
        routes={
            route: _route(
                route,
                records,
                now=now,
                max_age_hours=max_age_hours,
                log=log,
                evaluation=evaluation,
            )
            for route, records in sorted(by_route.items())
        },
        null_reasons=reasons,
    )


__all__ = [
    "CATALOGUE_ROUTES",
    "CIRCUIT_TRANSPORTS",
    "DEFAULT_MAX_AGE_HOURS",
    "FIXED_TRANSPORTS",
    "KNOWN_TELEMETRY_SCHEMAS",
    "PURPOSE_ROUTE",
    "TOOL_VERSION",
    "VERSIONED_TRANSPORTS",
    "InputError",
    "build_report",
    "drop_outer_chain_records",
]
