"""Audit-Chain-Integritäts-Status (#314): Tamper-Evidence des Attestation-Ledgers.

``artifacts/truth/attestation_ledger.jsonl`` ist die forward-only Hash-Kette über
attestierte Aussagen (:mod:`app.truth.ledger`, ADR 0013 Tier 1). Dieses Modul
rechnet sie über :func:`app.truth.ledger.verify_ledger` nach — Payload-Hash,
Record-Hash und Verkettung je Record — und leitet einen kompakten,
dashboard-tauglichen *Integritäts*-Status ab. Es ist die dritte Truth-Layer-KPI
neben Replay-Status (Portfolio-Rekonstruierbarkeit) und OTS-Integrity
(On-Chain-Anchoring).

**Quellenwechsel 2026-09-04.** Bis dahin las dieses Modul
``decision_journal_chain.jsonl``. Dieser Strom hatte keinen Produktionsschreiber
(``append_chain_entry`` wurde nur von Tests aufgerufen), also konnte das KPI
strukturell nur ``empty`` liefern: ein Integritätsindikator, der nicht
fehlschlagen kann, ist schlimmer als keiner. Die Decision-Kette wurde entfernt;
das Panel zeigt jetzt die Kette, die in Produktion wirklich fortgeschrieben wird.

EHRLICH:
  * ``empty``       — noch kein Ledger-Record (frische Installation, kein Fehler);
  * ``ok``          — N Records, lückenlos verkettet, Hashes nachgerechnet;
  * ``broken``      — echtes Tamper erkannt (Verkettung/Payload/Record-Hash),
                      mit Anzahl + erstem Fehler;
  * ``unavailable`` — Ledger-Datei unlesbar (mit Grund).

Reine Ableitung (:func:`derive_audit_chain_status`); IO nur im dünnen
:func:`load_audit_chain_status`-Wrapper. Fail-soft, nie 500.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.truth.ledger import DEFAULT_TRUTH_LEDGER_PATH, verify_ledger

#: Fehlergrund aus ``read_verified_ledger``, der KEIN Tamper ist, sondern ein
#: Lesefehler (Datei fehlt/ist keine Datei/kein UTF-8) — das ist ``unavailable``.
_UNREADABLE_PREFIX = "ledger unreadable"


@dataclass(frozen=True)
class AuditChainStatus:
    state: str  # ok | empty | broken | unavailable
    available: bool
    entries: int  # verifizierte Ledger-Records
    errors: int  # Anzahl echter Integritätsfehler
    first_error: str | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def derive_audit_chain_status(*, entries: int, errors: list[str]) -> AuditChainStatus:
    """Pure: Record-Count + Fehlerliste → Integritäts-Status.

    Jeder Fehler aus :func:`verify_ledger` ist ein echter Integritätsbefund
    (Seq-Lücke, gebrochene Verkettung, Payload-/Record-Hash-Mismatch) — es gibt
    hier bewusst keine "informative" Fehlerklasse, die ein rotes KPI grün redet.
    """
    if errors:
        return AuditChainStatus(
            state="broken",
            available=True,
            entries=entries,
            errors=len(errors),
            first_error=errors[0],
            reason="Tamper erkannt — der Attestation-Ledger ist kompromittiert.",
        )
    if entries == 0:
        return AuditChainStatus(
            state="empty",
            available=True,
            entries=0,
            errors=0,
            first_error=None,
            reason="Noch keine Attestierung im Ledger (kein Fehler).",
        )
    return AuditChainStatus(
        state="ok",
        available=True,
        entries=entries,
        errors=0,
        first_error=None,
        reason="",
    )


def load_audit_chain_status(
    ledger_path: Path = DEFAULT_TRUTH_LEDGER_PATH,
) -> AuditChainStatus:
    """IO-Wrapper: Ledger nachrechnen und Status ableiten.

    Fail-soft → ``unavailable`` (Panel degradiert, nie 500).
    """
    try:
        result = verify_ledger(ledger_path)
    except Exception as exc:  # noqa: BLE001 — Panel degradiert, nie 500
        return _unavailable(f"Ledger-Read-Fehler: {exc}")

    reasons = [str(e.get("reason", "")) for e in result.get("errors", [])]
    unreadable = [r for r in reasons if r.startswith(_UNREADABLE_PREFIX)]
    if unreadable:
        return _unavailable(f"Ledger-Read-Fehler: {unreadable[0]}")

    formatted = [
        f"seq {e.get('seq')}: {e.get('reason')}" for e in result.get("errors", []) if e is not None
    ]
    return derive_audit_chain_status(entries=int(result.get("records", 0)), errors=formatted)


def _unavailable(reason: str) -> AuditChainStatus:
    return AuditChainStatus(
        state="unavailable",
        available=False,
        entries=0,
        errors=0,
        first_error=None,
        reason=reason,
    )
