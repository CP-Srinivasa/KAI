#!/usr/bin/env python
"""Run reconciliation (read node, append outcomes only).

Zwei Journale, ein Timer. Name, Pfad und ``kai-ln-reconcile.timer`` bleiben
unveraendert: sie laufen bereits auf der Anlage, und ein zweiter Timer waere
ein zweiter Ort, an dem die Reconciliation ausfallen kann, ohne dass es
jemandem auffaellt.

* Zuerst die bestehende v2-Reconciliation (``app/lightning/reconciliation.py``)
  ueber ``artifacts/ln_ops_ledger_v2.jsonl``.
* Danach der Payment Control Plane (ADR 0018 §8) ueber
  ``artifacts/payments/payment_journal.jsonl``.

**Dieser Prozess sendet nie.** Er ruft ``lookup``, ``list_payments`` und
``invoice_status`` — Lesepfade. Das ist die Zusage aus ADR §5 (ein sendender
Prozess), und sie haengt nicht an Disziplin: ``pay`` wird im Payment-Paket an
genau einer Stelle gerufen (``PaymentService.execute``), und die kommt hier
nicht vor.

Exit-Code: 0 nur, wenn BEIDE Laeufe ``ok`` melden. Ein ``attention`` aus dem
Geld-Journal (Waisen-Settlement, ungeklaerter Send, Uhr-Sprung) ist ein Befund
— zusaetzlich zum Health-Check-Pfad, der den persistierten Zustand liest.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from app.core.payment_settings import PaymentSettings, get_payment_settings
from app.core.settings import get_settings
from app.lightning.adapter import _build_client
from app.lightning.client import LightningUnavailableError
from app.lightning.reconciliation import reconcile_ln_ops
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
) -> dict[str, Any]:
    """Der Payment-Teil des Laufs. Alle Argumente sind Test-Nahtstellen."""
    cfg = settings or get_payment_settings()
    money_journal = journal or PaymentJournal(cfg.resolved_journal_path())
    if journal is None:
        money_journal.open()
    report = await payment_reconcile.run(
        money_journal,
        rail or _build_payment_rail(cfg),
        settings=cfg,
        state_path=state_path,
    )
    return report.to_dict()


async def _main() -> int:
    cfg = get_settings().lightning
    client = None
    client_error = ""
    if not cfg.enabled:
        client_error = "lightning_disabled"
    else:
        try:
            # Reconciliation needs ListPayments only: always the READ credential,
            # never the pay-gated payment credential and never a write endpoint.
            client = _build_client(cfg, credential_scope="read")
        except LightningUnavailableError as exc:
            client_error = f"read_client_unavailable:{type(exc).__name__}"

    report = await reconcile_ln_ops(client=client, client_error=client_error)
    try:
        report["payments"] = await reconcile_payments()
    except Exception as exc:  # noqa: BLE001 - ein Fehler hier IST der Befund
        report["payments"] = {"status": "attention", "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    payments_ok = report["payments"].get("status") == "ok"
    return 0 if (report["status"] == "ok" and payments_ok) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
