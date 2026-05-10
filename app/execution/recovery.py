"""Crash-Recovery für Signal-to-Execution Pipeline.

Spec: docs/architecture/signal_to_execution_gap_analysis_20260510.md
      Aufgabenpaket 8 (Operator-Auftrag 2026-05-10).

Why
---
Wenn ``kai-server`` mid-cycle abstürzt oder neu gestartet wird, dürfen
keine Pending-Signale verloren gehen UND keine doppelten Orders erzeugt
werden. Dieses Modul stellt drei Recovery-Primitive bereit:

1. **collect_idempotency_keys_from_paper_audit()** — sammelt alle
   ``idempotency_key``-Werte die bereits einen erfolgreichen ``filled``-
   Event im paper-execution-audit produziert haben. Verhindert Doppel-
   Fill nach Crash (Operator-Auftrag Aufgabenpaket-9 Test #13).

2. **recover_pending_signals()** — scannt ``bridge_pending_orders.jsonl``
   und gibt envelope-records zurück deren last_stage NICHT terminal ist.
   Diese Signale müssen vom EntryWatcher / Bridge weiter beobachtet
   werden (Operator-Auftrag Aufgabenpaket-9 Test #12).

3. **detect_orphaned_submitted()** — findet ``ORDER_SUBMITTED``-records
   ohne nachfolgendes ``POSITION_OPEN``. Diese sind Crash-Kandidaten,
   die vor Re-Submit per ``has_idempotency_collision()`` geprüft werden
   müssen.

Vertrag
-------
- **Pure observation.** Schreibt NICHTS, modifiziert KEINEN State —
  nur Read auf Audit-Streams.
- **Tolerant gegen malformed lines.** Nutzt ``read_jsonl_tolerant`` aus
  ``app.storage.jsonl_io`` (D-194 Pattern).
- **Idempotency-Pflicht.** Konsumenten dürfen ein recoveries Pending-
  Signal nur dann re-submitten wenn ``has_idempotency_collision()`` False
  ist. Das ist die Akzeptanz für Test #13.
- **Single-Pass.** Eine Audit-Lese pro Recovery-Zyklus, O(n) wo n =
  Audit-Records. Caller cached die zurückgegebenen Sets/Lists wenn nötig.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)


# Default-Pfade — alle relativ zum CWD beim Boot von kai-server.
_BRIDGE_LOG_DEFAULT: Final[Path] = Path("artifacts/bridge_pending_orders.jsonl")
_PAPER_AUDIT_DEFAULT: Final[Path] = Path("artifacts/paper_execution_audit.jsonl")
_ENVELOPE_LOG_DEFAULT: Final[Path] = Path("artifacts/telegram_message_envelope.jsonl")


# Bridge-Stages die Recovery NICHT mehr betrachtet (= Signal ist abgeschlossen).
_TERMINAL_BRIDGE_STAGES: Final[frozenset[str]] = frozenset(
    {
        "filled",
        "expired",
        "rejected_risk",
        "rejected_size",
        "rejected_incomplete",
        "rejected_short_unsupported",  # historical pre-V25
        "rejected_fill",
        "rejected_position_exists",
        "skipped_source",
    }
)


# Bridge-Stages die Recovery wieder unter Beobachtung stellen muss.
_RECOVERABLE_BRIDGE_STAGES: Final[frozenset[str]] = frozenset(
    {
        "pending",  # WAITING_FOR_ENTRY äquivalent
        "no_market_data",
        "price_outside_tolerance",
    }
)


@dataclass(frozen=True)
class RecoverableEnvelope:
    """Envelope-Record + last bridge stage für recovery-Konsumenten."""

    envelope_id: str
    correlation_id: str
    last_stage: str
    last_reason: str
    payload: dict[str, object]
    last_seen_utc: str
    raw_record: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class RecoveryResult:
    """Outcome eines vollständigen Recovery-Sweeps."""

    pending_signals_recovered: int
    orphaned_submitted_count: int
    idempotency_collisions: int
    open_positions_seen: int
    pending_envelope_ids: tuple[str, ...] = ()
    orphaned_idempotency_keys: tuple[str, ...] = ()


# ─── JSONL-IO ──────────────────────────────────────────────────────────────


def _read_jsonl_tolerant(path: Path) -> list[dict[str, object]]:
    """Toleranter JSONL-Read. Nutzt zentrale Helper wenn verfügbar, sonst
    inline mit Skipp-on-Malformed. Crash-Recovery muss bei korrupten
    Last-Lines durchlaufen — der Crash hat eventuell halbe Records
    geschrieben."""
    try:
        from app.storage.jsonl_io import read_jsonl_tolerant

        return list(read_jsonl_tolerant(path))
    except ImportError:
        # Fallback: inline tolerant read
        if not path.exists():
            return []
        records: list[dict[str, object]] = []
        try:
            for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    obj = json.loads(stripped)
                    if isinstance(obj, dict):
                        records.append(obj)
                except json.JSONDecodeError as exc:
                    logger.warning(
                        "[recovery] skipped malformed line %s:%d (%s)",
                        path,
                        line_no,
                        exc,
                    )
        except OSError as exc:
            logger.warning("[recovery] read %s failed: %s", path, exc)
        return records


# ─── Idempotency-Check ─────────────────────────────────────────────────────


def collect_idempotency_keys_from_paper_audit(
    audit_path: Path = _PAPER_AUDIT_DEFAULT,
) -> set[str]:
    """Sammelt alle ``idempotency_key``-Werte die in einem
    ``order_filled``-Event im paper-execution-audit gelandet sind.

    Konsumenten nutzen das Set um vor Re-Submit zu prüfen ob die Order
    bereits fertig war (Test #13: Crash nach ORDER_SUBMITTED → keine
    Doppel-Order).
    """
    keys: set[str] = set()
    for rec in _read_jsonl_tolerant(audit_path):
        event_type = rec.get("event_type")
        if event_type != "order_filled":
            continue
        # idempotency_key kann auf Top-Level oder nested liegen — prüfe beides
        idem = rec.get("idempotency_key")
        if not isinstance(idem, str):
            order = rec.get("order") if isinstance(rec.get("order"), dict) else None
            if order is not None:
                idem_nested = order.get("idempotency_key")
                if isinstance(idem_nested, str):
                    idem = idem_nested
        if isinstance(idem, str) and idem:
            keys.add(idem)
    return keys


def has_idempotency_collision(
    candidate_key: str,
    audit_path: Path = _PAPER_AUDIT_DEFAULT,
) -> bool:
    """True wenn ``candidate_key`` bereits einen filled-Event hat.

    Konvenienz-Wrapper für single-key-Checks. Bei multi-recovery besser
    ``collect_idempotency_keys_from_paper_audit()`` einmal cachen und
    direkt gegen das Set prüfen.
    """
    if not candidate_key:
        return False
    return candidate_key in collect_idempotency_keys_from_paper_audit(audit_path)


# ─── Pending-Signal-Recovery ───────────────────────────────────────────────


def _latest_stage_per_envelope(
    bridge_records: list[dict[str, object]],
) -> dict[str, dict[str, object]]:
    """Returns ``{envelope_id: latest_record}`` from append-only bridge log."""
    latest: dict[str, dict[str, object]] = {}
    for rec in bridge_records:
        env_id = rec.get("envelope_id")
        if not isinstance(env_id, str) or not env_id:
            continue
        latest[env_id] = rec
    return latest


def recover_pending_signals(
    bridge_log: Path = _BRIDGE_LOG_DEFAULT,
    envelope_log: Path = _ENVELOPE_LOG_DEFAULT,
) -> list[RecoverableEnvelope]:
    """Scannt ``bridge_pending_orders.jsonl`` und gibt envelopes zurück
    deren last_stage NICHT terminal ist.

    Recovery-Konsument: EntryWatcher / Bridge picks them up and resumes
    polling. Caller is responsible for re-instantiating WatchersExecutionEngine
    / re-attaching status-history.

    Returns
    -------
    Liste von RecoverableEnvelope mit envelope_id + correlation_id +
    last_stage + last_reason + payload + last_seen_utc.
    """
    bridge_records = _read_jsonl_tolerant(bridge_log)
    envelope_records = _read_jsonl_tolerant(envelope_log)

    latest_per_id = _latest_stage_per_envelope(bridge_records)

    # Build envelope_id → envelope-payload-dict for cross-reference
    envelopes_by_id: dict[str, dict[str, object]] = {}
    for env_rec in envelope_records:
        eid = env_rec.get("envelope_id")
        if isinstance(eid, str) and eid:
            envelopes_by_id[eid] = env_rec

    out: list[RecoverableEnvelope] = []
    for envelope_id, latest_bridge_rec in latest_per_id.items():
        stage = latest_bridge_rec.get("stage")
        if not isinstance(stage, str):
            continue
        if stage in _TERMINAL_BRIDGE_STAGES:
            continue
        # Recoverable: get matching envelope-payload
        env_rec = envelopes_by_id.get(envelope_id, {})
        payload = env_rec.get("payload") if isinstance(env_rec.get("payload"), dict) else {}
        correlation_id = ""
        if isinstance(latest_bridge_rec.get("correlation_id"), str):
            correlation_id = str(latest_bridge_rec["correlation_id"])
        elif isinstance(env_rec.get("correlation_id"), str):
            correlation_id = str(env_rec["correlation_id"])

        last_reason = ""
        if isinstance(latest_bridge_rec.get("reason"), str):
            last_reason = str(latest_bridge_rec["reason"])

        last_seen = ""
        if isinstance(latest_bridge_rec.get("timestamp_utc"), str):
            last_seen = str(latest_bridge_rec["timestamp_utc"])
        elif isinstance(latest_bridge_rec.get("emitted_at_utc"), str):
            last_seen = str(latest_bridge_rec["emitted_at_utc"])

        out.append(
            RecoverableEnvelope(
                envelope_id=envelope_id,
                correlation_id=correlation_id,
                last_stage=stage,
                last_reason=last_reason,
                payload=dict(payload) if isinstance(payload, dict) else {},
                last_seen_utc=last_seen,
                raw_record=dict(latest_bridge_rec),
            )
        )
    return out


# ─── Orphaned-Submitted-Detection ──────────────────────────────────────────


def detect_orphaned_submitted(
    bridge_log: Path = _BRIDGE_LOG_DEFAULT,
    audit_path: Path = _PAPER_AUDIT_DEFAULT,
) -> list[str]:
    """Findet idempotency-keys die in der Bridge als ``ORDER_SUBMITTED``
    geloggt sind, aber keinen ``order_filled``-Event im paper-audit
    haben.

    Diese Records sind Crash-Kandidaten: die Bridge hat den Order
    submittet, aber wir wissen nicht ob er gefüllt wurde. Konsument
    MUSS vor Re-Submit ``has_idempotency_collision()`` prüfen — falls
    ja, war der Crash zwischen ``filled``-Audit-Write und Bridge-Stage-
    Update, und der Re-Submit würde Doppel-Fill erzeugen.

    Returns: Liste von idempotency_keys die orphaned-submitted-status haben.
    """
    bridge_records = _read_jsonl_tolerant(bridge_log)
    filled_keys = collect_idempotency_keys_from_paper_audit(audit_path)

    submitted_keys: set[str] = set()
    for rec in bridge_records:
        # bridge schreibt entweder lifecycle_state oder stage
        state = rec.get("lifecycle_state") or rec.get("stage")
        if not isinstance(state, str):
            continue
        if state.upper() != "ORDER_SUBMITTED":
            continue
        idem = (
            rec.get("idempotency_key") or rec.get("order_intent", {}).get("idempotency_key")
            if isinstance(rec.get("order_intent"), dict)
            else rec.get("idempotency_key")
        )
        if isinstance(idem, str) and idem:
            submitted_keys.add(idem)

    # Orphaned = submitted but not yet filled
    return sorted(submitted_keys - filled_keys)


# ─── Top-Level Sweep ───────────────────────────────────────────────────────


def run_recovery_sweep(
    *,
    bridge_log: Path = _BRIDGE_LOG_DEFAULT,
    envelope_log: Path = _ENVELOPE_LOG_DEFAULT,
    audit_path: Path = _PAPER_AUDIT_DEFAULT,
    candidate_idempotency_keys: list[str] | None = None,
) -> RecoveryResult:
    """One-pass Recovery-Sweep — combine pending-signals, orphaned-submitted,
    idempotency-collisions in einem Result.

    Use case: ``kai-server`` Boot-Hook ruft das einmal auf, schreibt das
    Result ins Boot-Log, und übergibt ``pending_envelope_ids`` an den
    EntryWatcher zum re-attaching.

    Wenn ``candidate_idempotency_keys`` gegeben, wird gezählt wie viele
    davon im paper-audit als bereits-filled erkannt werden — typisch
    aufgerufen mit den Keys der gerade-recovered envelopes.
    """
    pending = recover_pending_signals(bridge_log=bridge_log, envelope_log=envelope_log)
    orphaned_keys = detect_orphaned_submitted(bridge_log=bridge_log, audit_path=audit_path)
    filled_keys_cache = collect_idempotency_keys_from_paper_audit(audit_path)

    collisions = 0
    if candidate_idempotency_keys:
        for k in candidate_idempotency_keys:
            if k in filled_keys_cache:
                collisions += 1

    # Open-position count via existing replay
    try:
        from app.execution.audit_replay import replay_paper_audit

        replay = replay_paper_audit(audit_path)
        open_positions = len(replay.positions) if hasattr(replay, "positions") else 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("[recovery] paper-audit replay failed: %s", exc)
        open_positions = 0

    return RecoveryResult(
        pending_signals_recovered=len(pending),
        orphaned_submitted_count=len(orphaned_keys),
        idempotency_collisions=collisions,
        open_positions_seen=open_positions,
        pending_envelope_ids=tuple(p.envelope_id for p in pending),
        orphaned_idempotency_keys=tuple(orphaned_keys),
    )


__all__ = [
    "RecoverableEnvelope",
    "RecoveryResult",
    "collect_idempotency_keys_from_paper_audit",
    "detect_orphaned_submitted",
    "has_idempotency_collision",
    "recover_pending_signals",
    "run_recovery_sweep",
]
