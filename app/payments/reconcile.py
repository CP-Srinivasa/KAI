"""Reconciliation in beide Richtungen (ADR 0018 §8).

Der einzige Weg zurueck aus ``RECONCILIATION_REQUIRED``. Er fuehrt ueber
Node-Evidenz und ueber nichts sonst.

**Drei Richtungen, ein Lauf:**

1. **Vorwaerts** — jeder nicht-terminale Intent wird per ``rail.lookup`` gegen
   den Node gehalten. ``SUCCEEDED`` wird ``SETTLED`` (mit Betrag, Gebuehr und
   Proof-Hash), ``FAILED`` wird terminal, alles andere bleibt in der Klaerung.
2. **Rueckwaerts** — was der Rail bewegt hat, ohne dass ein Intent es
   beauftragt haette, ist ein ``orphan_settlement``. Genau einmal gemeldet:
   ein Alarm, der sich alle fuenf Minuten wiederholt, wird stummgeschaltet.
3. **Forderungen** — eine ausgestellte Invoice, die der Node als beglichen
   meldet, wird zu ``receivable_settled`` mit der eigenen Bestellreferenz.
4. **Journal gegen Journal** — solange der Altpfad mitlaeuft (ADR §12), wird
   jede Zahlung gemeldet, die BEIDE Buecher fuehren und die das alte nicht
   bewiesen abgeschlossen hat (:mod:`app.payments.reconcile_dual`). Diese
   Richtung fragt keinen Node; sie fragt die andere Buchfuehrung.

**Dieser Prozess sendet nie.** Er ruft ``lookup``, ``list_payments`` und
``invoice_status`` — Lesepfade. ``pay`` steht in genau einer Datei
(:mod:`app.payments.service`), und der Reconcile-Timer laeuft nicht durch sie.
Das ist die Zusage aus ADR §5: ein sendender Prozess, der Timer haengt nur
Outcomes an.

**Nur ein Statuswechsel schreibt.** Ein Timer im Fuenf-Minuten-Takt, der jedes
Mal denselben Zustand anhaengt, macht die Kette teuer und das Journal
unlesbar — und aus einem "unveraendert" wuerde optisch ein Ereignis.

**Der Uhr-Sprung-Guard** (Red-Team D-06) steht hier, weil ``EXPIRED`` terminal
ist. Eine korrigierte Systemzeit wuerde offene Intents unwiderruflich
verfallen lassen; darum vergleicht der Lauf die Wall-Clock-Differenz mit der
monotonen und setzt Ablauf-Uebergaenge bei Verdacht aus.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from app.core.payment_settings import PaymentSettings
from app.payments.journal import PaymentJournal
from app.payments.rail import PaymentRail
from app.payments.reconcile_dual import dual_journal_pass
from app.payments.reconcile_passes import backward, expire, forward, receivables, unresolved
from app.payments.reconcile_types import (
    DEFAULT_CLOCK_SKEW_TOLERANCE_S,
    STATE_FILENAME,
    ReconcileReport,
    ReconcileState,
    boot_reference,
    load_state,
    save_state,
)

Clock = Callable[[], datetime]
Monotonic = Callable[[], float]


async def run(
    journal: PaymentJournal,
    rail: PaymentRail,
    *,
    settings: PaymentSettings,
    clock: Clock | None = None,
    monotonic: Monotonic | None = None,
    boot_ref: str | None = None,
    state_path: Path | None = None,
    legacy_path: Path | None = None,
    clock_skew_tolerance_s: float = DEFAULT_CLOCK_SKEW_TOLERANCE_S,
) -> ReconcileReport:
    """Ein Reconcile-Lauf. Liest den Node, schreibt hoechstens Outcomes."""
    now = (clock or (lambda: datetime.now(UTC)))()
    mono = (monotonic or time.monotonic)()
    boot = boot_reference() if boot_ref is None else boot_ref
    path = state_path or (journal.path.parent / STATE_FILENAME)

    previous = load_state(path)
    anomaly, expiry_enabled, skew = _clock_verdict(
        previous, now=now, mono=mono, boot=boot, tolerance=clock_skew_tolerance_s
    )

    journal.refresh_tail()
    counts: dict[str, int] = {}
    notes: list[str] = []

    if anomaly:
        journal.append(
            "clock_guard",
            "clock_anomaly",
            {"clock_skew_s": int(abs(skew)), "status": "attention"},
            ts=now,
        )
        notes.append("expiry suspended: wall clock and monotonic clock disagree")
    elif not expiry_enabled:
        notes.append("expiry suspended: no comparable monotonic baseline (first run or reboot)")

    checked = await forward(journal, rail, counts=counts, now=now)
    if expiry_enabled:
        expire(journal, counts=counts, now=now)
    listing, orphans = await backward(journal, rail, counts=counts, now=now, settings=settings)
    checked_receivables = await receivables(journal, rail, counts=counts, now=now)
    # Vierte Richtung, nur waehrend des Dual-Read (ADR §12): nicht Node gegen
    # Journal, sondern Journal gegen Journal. Rein lesend auf der Altseite.
    dual = dual_journal_pass(journal, counts=counts, now=now, legacy_path=legacy_path)

    unresolved_count = unresolved(journal)
    status = "attention" if (orphans or dual or anomaly or unresolved_count) else "ok"
    report = ReconcileReport(
        status=status,
        counts=counts,
        orphans=orphans,
        clock_anomaly=anomaly,
        expiry_enabled=expiry_enabled,
        checked_intents=checked,
        unresolved=unresolved_count,
        checked_receivables=checked_receivables,
        dual_conflicts=dual,
        window_enforced=listing.window_enforced,
        complete=listing.complete,
        ran_at=now.isoformat(),
        rail=rail.name,
        notes=tuple(notes),
    )
    save_state(
        path,
        ReconcileState(
            last_run_utc=now.isoformat(),
            last_monotonic=mono,
            boot_ref=boot,
            last_status=status,
            last_orphans=len(orphans),
            last_clock_anomaly=anomaly,
        ),
    )
    return report


# --------------------------------------------------------------------------- #
# Uhr
# --------------------------------------------------------------------------- #


def _clock_verdict(
    previous: ReconcileState, *, now: datetime, mono: float, boot: str, tolerance: float
) -> tuple[bool, bool, float]:
    """``(anomalie, ablauf_erlaubt, abweichung_s)``.

    Ohne vergleichbare Basislinie gibt es keine Anomalie — aber auch keinen
    Ablauf. Das ist der Unterschied zwischen "wir wissen, dass die Uhr sprang"
    und "wir wissen nichts ueber die Uhr"; nur die erste Aussage rechtfertigt
    einen Alarm, beide verbieten einen terminalen Uebergang.
    """
    if previous.last_monotonic is None or not previous.comparable_with(boot):
        return False, False, 0.0
    try:
        wall_delta = (now - datetime.fromisoformat(previous.last_run_utc)).total_seconds()
    except ValueError:
        return False, False, 0.0
    mono_delta = mono - previous.last_monotonic
    skew = wall_delta - mono_delta
    if abs(skew) > tolerance:
        return True, False, skew
    return False, True, skew


__all__ = ["ReconcileReport", "ReconcileState", "load_state", "run", "save_state"]
