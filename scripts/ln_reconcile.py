#!/usr/bin/env python
"""Run reconciliation (read node, append outcomes only).

Ein Journal, ein Timer. Der Lauf gilt seit ADR 0018 §12 (PR 2) nur noch dem
Payment Control Plane (ADR §8) ueber ``artifacts/payments/payment_journal.jsonl``.
Die v2-Haelfte ueber ``artifacts/ln_ops_ledger_v2.jsonl`` ist entfallen: das
alte Journal ist Archiv, es hat weder Schreiber noch Leser.

Name, Pfad und ``kai-ln-reconcile.timer`` bleiben trotzdem unveraendert. Sie
laufen bereits auf der Anlage, und ein umbenannter Timer waere ein
Deploy-Schritt mit genau einem Ergebnis: ein Fenster, in dem der Geldpfad
keinen Reconciler hat, weil die alte Unit weg und die neue noch nicht scharf
ist.

**Dieser Prozess sendet nie.** Er ruft ``lookup``, ``list_payments`` und
``invoice_status`` — Lesepfade. Das ist die Zusage aus ADR §5 (ein sendender
Prozess), und sie haengt nicht an Disziplin: ``pay`` wird im Payment-Paket an
genau einer Stelle gerufen (``PaymentService.execute``), und die kommt hier
nicht vor.

Exit-Code: 0 nur bei ``ok``. Ein ``attention`` (Waisen-Settlement, ungeklaerter
Send, Uhr-Sprung) ist ein Befund — zusaetzlich zum Health-Check-Pfad, der den
persistierten Zustand liest und seit PR 2 auch dessen ALTER prueft
(``check_payment_reconciliation``).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from app.core.payment_settings import PaymentSettings, get_payment_settings
from app.core.settings import get_settings
from app.payments import reconcile as payment_reconcile
from app.payments.journal import PaymentJournal
from app.payments.rail import PaymentRail


def _build_payment_rail(settings: PaymentSettings) -> PaymentRail:
    """Der Rail fuer diesen Lauf — in SIMULATION ohne jeden Node-Kontakt."""
    if settings.mode == "simulation":
        from app.payments.rails.simulation import SimulationRail

        return SimulationRail()
    from app.payments.rails.lightning import LightningRail

    return LightningRail(
        payment_settings=settings,
        lightning_settings=get_settings().lightning,
    )


async def reconcile_payments(
    *,
    journal: PaymentJournal | None = None,
    rail: PaymentRail | None = None,
    settings: PaymentSettings | None = None,
    state_path: Path | None = None,
    clock: payment_reconcile.Clock | None = None,
) -> dict[str, Any]:
    """Der Payment-Teil des Laufs. Alle Argumente sind Test-Nahtstellen.

    ``clock`` gehoert dazu: das Rueckwaerts-Fenster (``max_inflight_window_s``)
    haengt an der Uhr — ein Test mit fester Rail-Zeit und echter Wanduhr wurde
    am 2026-09-04 12:00 UTC still gruen-auf-ok (Zahlung aus dem Fenster gefallen).
    """
    cfg = settings or get_payment_settings()
    money_journal = journal or PaymentJournal(cfg.resolved_journal_path())
    if journal is None:
        money_journal.open()
    report = await payment_reconcile.run(
        money_journal,
        rail or _build_payment_rail(cfg),
        settings=cfg,
        state_path=state_path,
        clock=clock,
    )
    return report.to_dict()


async def _main() -> int:
    try:
        report = await reconcile_payments()
    except Exception as exc:  # noqa: BLE001 - ein Fehler hier IST der Befund
        report = {"status": "attention", "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
