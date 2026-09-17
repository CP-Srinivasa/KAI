"""Zeilen fuer die Uebersichtskarte "Letzte Directional Alerts".

Ausgelagert aus ``dashboard.py`` (God-File, Baseline 3015 Zeilen, Ratchet
``scripts/godfile_ratchet.py``): der beruehrte Abschnitt wandert als eigenes
Modul mit Test hierher, statt die Baseline anzuheben.

2026-09-15 DALI v2.1: ``source_name`` kommt neu dazu. ``AlertAuditRecord``
traegt das Feld seit 2026-05-10, die Uebersicht zeigte bis dahin nur ein
12-Zeichen-Hash-Praefix der Dokument-ID — als "Quelle" unlesbar. Der Schluessel
ist IMMER vorhanden (``None`` bei alten Records), damit das Frontend nie
zwischen "fehlt" und "unbekannt" raten muss.

2026-09-17 Audit P0-4: ``priority_basis`` sagt, WOHER eine Prioritaet kommt --
oder warum keine da ist. ``priority`` ist ein Analysewert (1-10). Der
TradingView-Webhook liefert keinen; die Bridge schreibt deshalb bewusst keinen
(``hold_metrics`` korreliert Prioritaet mit Treffern, ein Platzhalter wuerde
diese Kennzahl faelschen). Ohne das Feld zeigte die Karte fuer 77 % der Zeilen
einen stummen Strich, der wie ein Datenfehler aussah.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

DOC_ID_PREFIX_LEN = 12
DISPATCHED_AT_LEN = 16  # "YYYY-MM-DDTHH:MM"

#: Kanaele, die per Konstruktion keine Analyse-Prioritaet tragen.
WEBHOOK_CHANNELS = frozenset({"tradingview_webhook"})


def priority_basis(record: Mapping[str, Any]) -> str:
    """``analysis`` (Wert vorhanden) · ``webhook`` (nicht anwendbar) · ``unknown``."""
    if record.get("priority") is not None:
        return "analysis"
    if record.get("channel") in WEBHOOK_CHANNELS:
        return "webhook"
    return "unknown"


def recent_alert_rows(
    records: Iterable[Mapping[str, Any]],
    outcomes_by_doc: Mapping[str, str],
) -> list[dict[str, Any]]:
    """Juengste zuerst; jede Zeile traegt genau die Felder des Frontend-Vertrags
    (``DashboardQuality.recent_alerts`` in ``web/src/lib/api.ts``)."""
    rows: list[dict[str, Any]] = []
    for r in reversed(list(records)):
        doc_id = str(r.get("document_id", "") or "")
        rows.append(
            {
                "doc_id": doc_id[:DOC_ID_PREFIX_LEN],
                "sentiment": r.get("sentiment_label", ""),
                "priority": r.get("priority"),
                "assets": r.get("affected_assets", []),
                "source_name": r.get("source_name"),
                "priority_basis": priority_basis(r),
                "dispatched_at": str(r.get("dispatched_at", "") or "")[:DISPATCHED_AT_LEN],
                "outcome": outcomes_by_doc.get(doc_id, ""),
            }
        )
    return rows
