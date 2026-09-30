"""Kanonisches JSON fuer Maschinen, Markdown in Klartext fuer den Operator."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Final

from scripts.litellm_route_report.models import (
    RouteAcceptanceReport,
    RouteReport,
    RouteStatus,
    TransportEvidence,
)

_TRANSPORT_LABEL: Final = {
    "litellm": "LiteLLM",
    "direct": "Direkt",
    "unbekannt": "Transport unbekannt",
}

_MISSING_LABEL: Final = {
    "letzter_erfolg": "kein erfolgreicher Aufruf",
    "identitaet": "Identität unbelegt",
    "kosten": "Kosten unbekannt",
    "retries": "Wiederholungen nicht erfasst",
    "fallback": "Fallback nicht erfasst",
    "circuit": "Circuit-Zustand nicht erfasst",
    "version": "Transportversion unbelegt",
    "evidenzalter": "Evidenz veraltet",
}

_ISSUE_LABEL: Final = {
    "JSON_DEFEKT": "kein gültiges JSON",
    "KEIN_OBJEKT": "kein JSON-Objekt",
    "UNBEKANNTE_SCHEMA_VERSION": "unbekannte Schemaversion",
    "ZEITSTEMPEL_UNGUELTIG": "Zeitstempel fehlt oder ohne Zeitzone",
    "OK_FEHLT": "ohne ok-Angabe",
}


def canonical_json(report: RouteAcceptanceReport) -> str:
    """Stabil: sortierte Schluessel, endliche Zahlen, keine Pfade des Operators."""
    return (
        json.dumps(report.to_dict(), sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)
        + "\n"
    )


def _transport(name: str) -> str:
    return _TRANSPORT_LABEL.get(name, f"Transport {name}")


def _zahl(value: float, stellen: int = 1) -> str:
    return f"{value:.{stellen}f}".replace(".", ",")


def _usd(value: float) -> str:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text.replace(".", ",") + " USD"


def _zeit(iso: str | None) -> str:
    if iso is None:
        return "–"
    return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M UTC")


def _liste(counts: dict[str, int]) -> str:
    return ", ".join(f"{key} {count}×" for key, count in counts.items())


def _zelle(text: str) -> str:
    return text.replace("|", "\\|")


def _aufrufe(count: int) -> str:
    return "1 Aufruf" if count == 1 else f"{count} Aufrufe"


def _kurz(section: TransportEvidence) -> str:
    name = _transport(section.transport)
    if section.calls == 0:
        return f"{name}: kein Aufruf"
    erfolg = (
        f"letzter Erfolg {_zeit(section.last_success_at)}"
        if section.last_success_at
        else "kein Erfolg"
    )
    return f"{name}: {_aufrufe(section.calls)}, {erfolg}"


def _routenzeile(route: RouteReport) -> str:
    kopf = f"- {route.status.value} · {route.logical_route}"
    if route.status is RouteStatus.KEINE_EVIDENZ:
        return f"{kopf} — kein Aufruf in den Eingaben, weder über LiteLLM noch direkt."
    teile = " · ".join(_kurz(section) for section in route.transports.values())
    zeile = f"{kopf} — {teile}."
    fehlend = [
        f"{_transport(section.transport)}: "
        + ", ".join(_MISSING_LABEL.get(item, item) for item in section.missing)
        for section in route.transports.values()
        if section.missing
    ]
    if fehlend:
        zeile += " Es fehlt: " + "; ".join(fehlend) + "."
    return zeile


def _grund(section: TransportEvidence, key: str) -> str:
    return "nicht belegt: " + section.null_reasons.get(key, "ohne Angabe")


def _release(section: TransportEvidence) -> str:
    version = section.version
    if version.release_sha is None:
        return _grund(section, "version.release_sha")
    return f"{version.release_sha[:12]} ({version.release_source})"


def _version(section: TransportEvidence) -> str:
    version = section.version
    if version.transport_version is None:
        return _grund(section, "version.transport_version")
    return f"{version.transport_version}, Manifest {version.transport_manifest or '(ohne)'}"


def _zeilen_details(section: TransportEvidence) -> dict[str, str]:
    if section.calls == 0:
        leer = "–"
        return {
            "Zustand": "keine Evidenz",
            "Aufrufe": "0",
            "Version": _version(section),
            "Release-SHA": _grund(section, "version.release_sha"),
            **dict.fromkeys(
                (
                    "Letzter Erfolg",
                    "Identität",
                    "Kosten",
                    "Fehler",
                    "Wiederholungen",
                    "Fallback",
                    "Circuit",
                    "Evidenzalter",
                ),
                leer,
            ),
        }
    ident, cost, failure, age = (
        section.identity,
        section.cost,
        section.failure,
        section.evidence_age,
    )
    zustand = "belegt" if section.status is RouteStatus.BELEGT else "lückenhaft"
    if section.missing:
        zustand += " (" + ", ".join(_MISSING_LABEL.get(m, m) for m in section.missing) + ")"
    aufrufe = str(section.calls)
    if section.modes:
        aufrufe += f" (Modus: {_liste(section.modes)})"
    if section.route_from_purpose:
        aufrufe += f"; Route bei {section.route_from_purpose} aus dem Zweck abgeleitet"

    identitaet = (
        f"{ident.proven} von {section.calls} belegt ({_zahl((ident.share or 0) * 100, 0)} %)"
    )
    if ident.actual:
        identitaet += f": {_liste(ident.actual)}"
    if ident.unproven_claimed:
        identitaet += f"; unbelegt (angefordert/konfiguriert): {_liste(ident.unproven_claimed)}"

    kosten = f"{cost.known} bekannt, {cost.unknown} unbekannt"
    if cost.failed_without_usage:
        kosten += f", {cost.failed_without_usage} Fehlversuche ohne Verbrauch"
    if cost.sum_known_usd is not None and cost.median_usd is not None:
        kosten += f"; Summe {_usd(cost.sum_known_usd)}"
        if not cost.sum_complete:
            kosten += " (nur bekannte)"
        kosten += f", Median {_usd(cost.median_usd)}"
    else:
        kosten += "; Summe und Median nicht belegt"

    fehler = (
        f"{failure.failures} ohne Ergebnis: {_liste(failure.error_classes)}"
        if failure.failures
        else "keine"
    )
    wiederholung = (
        f"{failure.retry_rows} Aufrufe mit Wiederholung, höchstens {failure.max_retry_count}"
        if failure.retry_rows
        else "keine"
    )
    if failure.retry_unknown:
        wiederholung += f"; bei {failure.retry_unknown} nicht erfasst"
    if failure.transport_retries_total:
        wiederholung += f"; zusätzlich im Transport: {failure.transport_retries_total}"
    if failure.fallback_rate is not None:
        known_rows = section.calls - failure.fallback_unknown
        fallback = (
            f"{failure.fallbacks} von {known_rows} ({_zahl(failure.fallback_rate * 100, 0)} %)"
        )
        if failure.fallback_unknown:
            fallback += f"; bei {failure.fallback_unknown} nicht erfasst"
    else:
        fallback = _grund(section, "failure.fallback_rate")
    if failure.circuit_states:
        circuit = _liste(failure.circuit_states)
        if failure.circuit_unknown:
            circuit += f"; bei {failure.circuit_unknown} nicht erfasst"
    else:
        circuit = section.null_reasons.get("failure.circuit_states", "–")
    alter = "–"
    if age.newest_age_hours is not None and age.oldest_age_hours is not None:
        alter = (
            f"jüngster Beleg {_zahl(age.newest_age_hours)} h, "
            f"ältester {_zahl(age.oldest_age_hours)} h"
        )
        if age.stale:
            alter += " — veraltet"
    return {
        "Zustand": zustand,
        "Aufrufe": aufrufe,
        "Version": _version(section),
        "Release-SHA": _release(section),
        "Letzter Erfolg": _zeit(section.last_success_at)
        if section.last_success_at
        else "kein erfolgreicher Aufruf",
        "Identität": identitaet,
        "Kosten": kosten,
        "Fehler": fehler,
        "Wiederholungen": wiederholung,
        "Fallback": fallback,
        "Circuit": circuit,
        "Evidenzalter": alter,
    }


def _details(route: RouteReport) -> list[str]:
    spalten = list(route.transports.values())
    tabellen = [_zeilen_details(section) for section in spalten]
    lines = [
        f"### {route.logical_route} — {route.status.value}",
        "",
        "| | " + " | ".join(_transport(section.transport) for section in spalten) + " |",
        "|---|" + "---|" * len(spalten),
    ]
    for key in tabellen[0]:
        lines.append(
            f"| {key} | " + " | ".join(_zelle(tabelle[key]) for tabelle in tabellen) + " |"
        )
    decision = route.eval_decision
    if decision is not None:
        gruende = ", ".join(decision.get("reasons") or []) or "ohne Gründe"
        bereit = {True: "ja", False: "nein"}.get(decision.get("primary_ready"), "nicht angegeben")
        lines += [
            "",
            f"Eval-Report (übernommen): {decision.get('status') or 'ohne Status'} "
            f"({gruende}); PRIMARY-reif laut Report: {bereit}.",
        ]
    if not route.in_catalogue:
        lines += ["", "Diese Route steht nicht im Routenkatalog (app/ai/routes.py)."]
    return [*lines, ""]


def _eingaben(report: RouteAcceptanceReport) -> list[str]:
    tele = report.telemetry
    verworfen = sum(tele.issues.values())
    zeile = (
        f"- Telemetrie: {len(tele.files)} Datei(en), {tele.rows_read} Zeilen gelesen, "
        f"{tele.rows_used} ausgewertet"
    )
    if verworfen:
        zeile += "; verworfen: " + ", ".join(
            f"{count}× {_ISSUE_LABEL.get(code, code)}" for code, count in tele.issues.items()
        )
    if tele.outer_chain_rows_dropped:
        zeile += f"; {tele.outer_chain_rows_dropped} äußere Kettenzeilen nicht doppelt gezählt"
    if tele.unattributed_rows:
        zeile += f"; {tele.unattributed_rows} Zeilen ohne Route und ohne bekannten Zweck"
    lines = [zeile + "."]

    log = report.transport_log
    if log is None:
        lines.append("- Transport-Log: nicht angegeben, die LiteLLM-Version ist damit unbelegt.")
    elif not log.last:
        lines.append("- Transport-Log: keine TRANSPORT_VERIFIED-Zeile gefunden.")
    else:
        for name, beleg in log.last.items():
            wann = (
                f"geprüft {_zeit(beleg.verified_at)}"
                if beleg.verified_at
                else "Zeile ohne Zeitstempel"
            )
            lines.append(
                f"- Transport-Log: {log.verified_lines} TRANSPORT_VERIFIED-Zeile(n), zuletzt "
                f"{name} {beleg.version or '(ohne Version)'}, Manifest "
                f"{beleg.manifest or '(ohne)'}, Baum {beleg.tree or '(ohne)'}; {wann}."
            )

    evaluation = report.eval_report
    if evaluation is None:
        lines.append("- Eval-Report: nicht angegeben.")
    else:
        alter = f", {_zahl(evaluation.age_hours)} h alt" if evaluation.age_hours is not None else ""
        reif = (
            ", ".join(evaluation.primary_ready_routes)
            if evaluation.primary_ready_routes
            else "keine"
        )
        zeile = (
            f"- Eval-Report: {evaluation.schema_version}, erzeugt "
            f"{_zeit(evaluation.generated_at)}{alter}; PRIMARY-reif laut Report: {reif}."
        )
        if evaluation.runtime_evidence is None:
            zeile += (
                " Laufzeitnachweise: "
                + evaluation.null_reasons.get("runtime_evidence", "keine")
                + "."
            )
        else:
            belegt = [
                f"{name} (Version {entry.get('version') or 'ohne'})"
                for name, entry in evaluation.runtime_evidence.items()
                if isinstance(entry, dict) and entry.get("proven") and entry.get("referenced")
            ]
            offen = [
                name
                for name, entry in evaluation.runtime_evidence.items()
                if isinstance(entry, dict) and not (entry.get("proven") and entry.get("referenced"))
            ]
            zeile += " Laufzeitnachweise mit Artefakt: " + (", ".join(belegt) or "keine")
            zeile += "; ohne Artefakt oder nicht belegt: " + (", ".join(offen) or "keine") + "."
        lines.append(zeile)
    return lines


def markdown_summary(report: RouteAcceptanceReport) -> str:
    routes = [report.routes[name] for name in sorted(report.routes)]
    lines = [
        "# LiteLLM-Routen-Abnahmebericht",
        "",
        f"Stand {_zeit(report.generated_at)}. Evidenz gilt als veraltet, wenn der jüngste "
        f"Beleg älter als {_zahl(report.max_evidence_age_hours, 0)} h ist.",
        "",
        "Dieser Bericht trifft keine Freigabe und aktiviert nichts. Er zeigt nur, was die "
        "Eingaben belegen; was fehlt, steht als fehlend da und nie als 0.",
        "",
        "## Routen",
        "",
        *(_routenzeile(route) for route in routes),
        "",
        "## Eingaben",
        "",
        *_eingaben(report),
        "",
        "## Details je Route",
        "",
    ]
    for route in routes:
        if route.status is not RouteStatus.KEINE_EVIDENZ:
            lines.extend(_details(route))
    return "\n".join(lines).rstrip("\n") + "\n"


__all__ = ["canonical_json", "markdown_summary"]
