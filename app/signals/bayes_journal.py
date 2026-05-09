"""Append-only Audit-Sidecar für Bayesian-Confidence-Reports.

Ein Bayes-Report ist *zu reichhaltig* für das DECISION_SCHEMA (verschachtelte
Beitragslisten, Erklärungs-Strings).  Statt das kanonische Decision-Journal
aufzuweichen, schreibt diese Komponente einen separaten JSONL-Stream, der
über `decision_id` mit dem Decision-Journal verbunden bleibt.

Vertrag:
  - JSONL append-only (eine Zeile pro Auswertung).
  - Keine Mutation bestehender Zeilen.
  - Schreibfehler werden geloggt + verschluckt — Audit darf den Trade-Pfad
    niemals blocken (KAI Safety §4: "Fail gracefully").
  - Schema versioniert über ``schema_version`` (start: 1).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from app.signals.bayesian_confidence import ConfidenceReport

logger = logging.getLogger(__name__)

DEFAULT_BAYES_AUDIT_PATH: Path = Path("artifacts/bayes_confidence_audit.jsonl")
SCHEMA_VERSION: int = 1


class BayesAuditEntry(BaseModel):
    """Eine Zeile im Bayes-Audit-Journal."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int
    timestamp_utc: str
    decision_id: str
    symbol: str
    direction: str  # "long" | "short"
    report: dict[str, object]  # ConfidenceReport.model_dump()


def append_bayes_report(
    *,
    decision_id: str,
    symbol: str,
    direction: str,
    report: ConfidenceReport,
    path: Path | str = DEFAULT_BAYES_AUDIT_PATH,
) -> Path | None:
    """Append einen ConfidenceReport ans JSONL-Audit.

    Returns den geschriebenen Pfad bei Erfolg, sonst None (Schreibfehler
    geloggt).  Verzeichnis wird bei Bedarf angelegt.
    """
    resolved = Path(path)
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        entry = BayesAuditEntry(
            schema_version=SCHEMA_VERSION,
            timestamp_utc=datetime.now(UTC).isoformat(),
            decision_id=decision_id,
            symbol=symbol,
            direction=direction,
            report=report.model_dump(mode="json"),
        )
        line = json.dumps(entry.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
        with resolved.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return resolved
    except (OSError, ValueError) as exc:
        # ValueError fängt z. B. Windows-Pfade mit Null-Bytes ab.
        # Audit darf den Trade-Pfad niemals blockieren.
        logger.warning("[bayes-audit] write failed for %s: %s", decision_id, exc)
        return None


def load_bayes_reports(path: Path | str = DEFAULT_BAYES_AUDIT_PATH) -> list[BayesAuditEntry]:
    """Lese alle Audit-Zeilen.  Verworfen werden nur fehlerhafte Zeilen
    (geloggt).  Datei fehlt → leere Liste.
    """
    resolved = Path(path)
    if not resolved.exists():
        return []
    out: list[BayesAuditEntry] = []
    for line_no, raw in enumerate(resolved.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
            out.append(BayesAuditEntry.model_validate(payload))
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("[bayes-audit] skipped malformed row %s:%d (%s)", resolved, line_no, exc)
    return out


__all__ = [
    "DEFAULT_BAYES_AUDIT_PATH",
    "SCHEMA_VERSION",
    "BayesAuditEntry",
    "append_bayes_report",
    "load_bayes_reports",
]
