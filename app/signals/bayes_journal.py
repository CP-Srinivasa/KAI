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
    sanitize: bool = False,
) -> Path | None:
    """Append einen ConfidenceReport ans JSONL-Audit.

    Returns den geschriebenen Pfad bei Erfolg, sonst None (Schreibfehler
    geloggt).  Verzeichnis wird bei Bedarf angelegt.

    Wenn ``sanitize=True``: das ``report``-dict wird durch
    :func:`app.audit.sanitization.sanitize_value` geschickt, bevor es
    geschrieben wird (Secrets-Redaktion + Long-String-Truncation für
    z. B. ``residual_uncertainty_drivers``).  Default ``False``, damit
    bestehende Konsumenten/Tests Verhalten 1:1 erhalten — opt-in pro
    Aufrufer (z. B. SignalGenerator setzt es nach Migration).
    """
    resolved = Path(path)
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        report_payload: dict[str, object] = report.model_dump(mode="json")
        if sanitize:
            from app.audit.sanitization import sanitize_value

            sanitized = sanitize_value(report_payload)
            if isinstance(sanitized, dict):
                report_payload = sanitized
        entry = BayesAuditEntry(
            schema_version=SCHEMA_VERSION,
            timestamp_utc=datetime.now(UTC).isoformat(),
            decision_id=decision_id,
            symbol=symbol,
            direction=direction,
            report=report_payload,
        )
        line = json.dumps(entry.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
        # SAT-F-005 fix: cross-process exclusive lock.
        from app.core.file_lock import append_lock

        with append_lock(resolved):
            with resolved.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return resolved
    except (OSError, ValueError) as exc:
        # ValueError fängt z. B. Windows-Pfade mit Null-Bytes ab.
        # Audit darf den Trade-Pfad niemals blockieren.
        logger.warning("[bayes-audit] write failed for %s: %s", decision_id, exc)
        return None


def load_bayes_reports(
    path: Path | str = DEFAULT_BAYES_AUDIT_PATH,
    *,
    max_rows: int = 10_000,
) -> list[BayesAuditEntry]:
    """Streaming-Reader (SAT-F-003 fix): tail-window via deque, kein
    full-file-into-memory.  ``max_rows`` cap verhindert OOM bei
    multi-GB Audit-Files.
    """
    from collections import deque

    resolved = Path(path)
    if not resolved.exists():
        return []
    buf: deque[BayesAuditEntry] = deque(maxlen=max_rows)
    try:
        with resolved.open("r", encoding="utf-8") as fh:
            for line_no, raw in enumerate(fh, start=1):
                line = raw.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                    buf.append(BayesAuditEntry.model_validate(payload))
                except (json.JSONDecodeError, ValueError) as exc:
                    logger.warning(
                        "[bayes-audit] skipped malformed row %s:%d (%s)",
                        resolved,
                        line_no,
                        exc,
                    )
    except OSError as exc:
        logger.warning("[bayes-audit] read failed (%s): %s", resolved, exc)
        return []
    return list(buf)


__all__ = [
    "DEFAULT_BAYES_AUDIT_PATH",
    "SCHEMA_VERSION",
    "BayesAuditEntry",
    "append_bayes_report",
    "load_bayes_reports",
]
