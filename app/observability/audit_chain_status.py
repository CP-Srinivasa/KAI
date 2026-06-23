"""Audit-Chain-Integritäts-Status (#314): Tamper-Evidence des Decision-Journals.

``artifacts/decision_journal_chain.jsonl`` ist der hash-verkettete Tamper-
Evidence-Stream über ``artifacts/decision_journal.jsonl`` (siehe
:mod:`app.audit.decision_chain`). Dieses Modul fährt :func:`verify_chain`
darüber — inkl. Cross-Check der Record-Hashes gegen die Journal-Payloads — und
leitet einen kompakten, dashboard-tauglichen *Integritäts*-Status ab. Es ist die
dritte Truth-Layer-KPI neben Replay-Status (Portfolio-Rekonstruierbarkeit) und
OTS-Integrity (On-Chain-Anchoring).

EHRLICH:
  * ``empty``       — noch kein Chain-Entry (legitime Migration-Lücke, kein Fehler);
  * ``ok``          — N Entries, lückenlos verkettet, Record-Hashes konsistent;
  * ``broken``      — echtes Tamper erkannt (Chain-Bruch / Hash-Mismatch /
                      Record-Mismatch / Duplikat), mit Anzahl + erstem Fehler;
  * ``unavailable`` — Chain-Datei unlesbar (mit Grund).

``missing_journal_record`` (Chain-Entry ohne Journal-Payload) zählt NICHT als
Tamper — das ist die legitime Folge einer Journal-Rotation und wird separat als
``journal_gaps`` (informativ) gemeldet. So bleibt ein grünes KPI ehrlich und ein
rotes KPI bedeutet wirklich Manipulation.

Reine Ableitung (:func:`derive_audit_chain_status`); IO nur im dünnen
:func:`load_audit_chain_status`-Wrapper. Fail-soft, nie 500.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.audit.decision_chain import (
    iter_chain_entries,
    load_journal_records_for_verify,
    verify_chain,
)

# Kanonische Pfade (deckungsgleich mit app.audit.decision_chain-Docstring und
# app.agents.tools._helpers.DECISION_JOURNAL_DEFAULT_PATH).
DEFAULT_CHAIN_PATH = Path("artifacts/decision_journal_chain.jsonl")
DEFAULT_JOURNAL_PATH = Path("artifacts/decision_journal.jsonl")

# Fehler-Präfixe aus verify_chain, die ECHTES Tamper bedeuten (rot). Ein
# ``missing_journal_record`` ist bewusst NICHT dabei (Rotation, nicht Tamper).
_TAMPER_PREFIXES = (
    "chain_break",
    "chain_hash_mismatch",
    "record_hash_mismatch",
    "duplicate_chain_entry",
)


@dataclass(frozen=True)
class AuditChainStatus:
    state: str  # ok | empty | broken | unavailable
    available: bool
    entries: int
    errors: int  # Anzahl echter Tamper-Fehler
    first_error: str | None
    journal_gaps: int  # missing_journal_record (Rotation, informativ)
    cross_checked: bool  # ob Journal-Payloads zum Cross-Check vorlagen
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def derive_audit_chain_status(
    *, entries: int, errors: list[str], cross_checked: bool
) -> AuditChainStatus:
    """Pure: Entry-Count + ``verify_chain``-Fehlerliste → Integritäts-Status.

    Klassifiziert die Fehlerliste nach Präfix: echte Tamper-Fehler treiben
    ``broken``; ``missing_journal_record`` ist Rotation und nur informativ.
    """
    tamper = [e for e in errors if e.split(" ", 1)[0] in _TAMPER_PREFIXES]
    journal_gaps = sum(1 for e in errors if e.startswith("missing_journal_record"))

    if entries == 0:
        return AuditChainStatus(
            state="empty",
            available=True,
            entries=0,
            errors=0,
            first_error=None,
            journal_gaps=0,
            cross_checked=cross_checked,
            reason="Noch keine Decisions verkettet (Migration-Lücke, kein Fehler).",
        )
    if tamper:
        return AuditChainStatus(
            state="broken",
            available=True,
            entries=entries,
            errors=len(tamper),
            first_error=tamper[0],
            journal_gaps=journal_gaps,
            cross_checked=cross_checked,
            reason="Tamper erkannt — Decision-Audit-Trail kompromittiert.",
        )
    return AuditChainStatus(
        state="ok",
        available=True,
        entries=entries,
        errors=0,
        first_error=None,
        journal_gaps=journal_gaps,
        cross_checked=cross_checked,
        reason="",
    )


def load_audit_chain_status(
    chain_path: Path = DEFAULT_CHAIN_PATH,
    journal_path: Path = DEFAULT_JOURNAL_PATH,
) -> AuditChainStatus:
    """IO-Wrapper: Chain verifizieren (mit Journal-Cross-Check wenn vorhanden) und
    Status ableiten. Fail-soft → ``unavailable`` (Panel degradiert, nie 500)."""
    try:
        entries = sum(1 for _ in iter_chain_entries(chain_path))
        journal_records = load_journal_records_for_verify(journal_path)
        cross_checked = bool(journal_records)
        errors = verify_chain(
            chain_path=chain_path,
            journal_records=journal_records or None,
        )
    except Exception as exc:  # noqa: BLE001 — Panel degradiert, nie 500
        return AuditChainStatus(
            state="unavailable",
            available=False,
            entries=0,
            errors=0,
            first_error=None,
            journal_gaps=0,
            cross_checked=False,
            reason=f"Chain-Read-Fehler: {exc}",
        )
    return derive_audit_chain_status(entries=entries, errors=errors, cross_checked=cross_checked)
