"""Replay-SSOT-Status (#314): Gesundheit des Paper-Execution-Audit-Replays.

``artifacts/paper_execution_audit.jsonl`` ist KAIs Replay-SSOT — das kanonische
Event-Log, aus dem das Portfolio rekonstruiert wird. Dieses Modul fährt
:func:`app.execution.audit_replay.replay_paper_audit` darüber und leitet einen
kompakten, dashboard-tauglichen *Integritäts*-Status ab (NICHT Performance — der
realisierte PnL lebt im Portfolio-Block).

EHRLICH:
  * ``unavailable`` — Replay fehlgeschlagen / Audit-Datei fehlt (mit Grund);
  * ``degraded``    — Replay gelang, aber resiliente Skips (korrupte/Race-Zeilen)
                      ODER Lifecycle-Replay-Fehler traten auf;
  * ``ok``          — sauberer, vollständiger Replay ohne Skips/Fehler.

Reine Ableitung über dem Replay-Ergebnis (:func:`derive_replay_status`); IO nur
im dünnen :func:`load_replay_status`-Wrapper.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.execution.audit_replay import AuditReplayResult, replay_paper_audit

# Kanonischer Replay-SSOT-Pfad (deckungsgleich mit
# app.agents.tools._helpers.PAPER_EXECUTION_AUDIT_DEFAULT_PATH).
DEFAULT_AUDIT_PATH = Path("artifacts/paper_execution_audit.jsonl")


@dataclass(frozen=True)
class ReplayStatus:
    state: str  # ok | degraded | unavailable
    available: bool
    positions: int
    fills_replayed: int
    skipped_events: int
    lifecycle_errors: int
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def derive_replay_status(result: AuditReplayResult) -> ReplayStatus:
    """Pure: ``AuditReplayResult`` → kompakter Dashboard-Integritäts-Status."""
    skipped = len(result.skipped_events)
    lc_errors = len(result.lifecycle_replay_errors)
    if not result.available:
        return ReplayStatus(
            state="unavailable",
            available=False,
            positions=0,
            fills_replayed=0,
            skipped_events=skipped,
            lifecycle_errors=lc_errors,
            reason=result.error or "Replay nicht verfügbar (Audit-Datei fehlt?).",
        )
    state = "degraded" if (skipped > 0 or lc_errors > 0) else "ok"
    return ReplayStatus(
        state=state,
        available=True,
        positions=len(result.positions),
        fills_replayed=len(result.filled_idempotency_keys),
        skipped_events=skipped,
        lifecycle_errors=lc_errors,
        reason="",
    )


def load_replay_status(audit_path: Path = DEFAULT_AUDIT_PATH) -> ReplayStatus:
    """IO-Wrapper: Audit-Ledger replayen und Status ableiten. Fail-soft → ``unavailable``."""
    try:
        result = replay_paper_audit(audit_path)
    except Exception as exc:  # noqa: BLE001 — Panel degradiert, nie 500
        return ReplayStatus(
            state="unavailable",
            available=False,
            positions=0,
            fills_replayed=0,
            skipped_events=0,
            lifecycle_errors=0,
            reason=f"Replay-Fehler: {exc}",
        )
    return derive_replay_status(result)
