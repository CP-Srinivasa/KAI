"""Recovery for outgoing Lightning payments with an open write-ahead intent.

This module never sends.  It only asks LND's Router service for the durable
payment-hash state and closes journal intents when LND reports a terminal result.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any

from app.core.lightning_settings import LightningSettings
from app.lightning.adapter import _build_client
from app.lightning.client import LightningUnavailableError
from app.lightning.ops_ledger import (
    LightningOpsLedgerError,
    append_ln_op,
    read_open_ln_intents,
    verify_ln_ops_ledger,
)


async def reconcile_spent_today(
    *,
    cfg: LightningSettings,
    ledger_spent_sat: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Cross-check the journal's daily spend against LND's payment database.

    The policy consumes the larger value. This catches both directions of drift:
    a journal outcome missing after a crash, and a conservative open intent that
    LND has not yet terminally classified. Failed payments never spent value and
    are excluded; initiated/in-flight payments remain reserved.
    """
    current = (now or datetime.now(UTC)).astimezone(UTC)
    report: dict[str, Any] = {
        "ledger_spent_sat": max(0, int(ledger_spent_sat)),
        "lnd_spent_sat": None,
        "effective_spent_sat": max(0, int(ledger_spent_sat)),
        "gap_sat": None,
        "available": False,
    }
    if not cfg.enabled:
        return report
    payment_hex, payment_path = cfg.macaroon_credentials("payment")
    if not payment_hex.strip() and not payment_path.strip():
        return report
    try:
        client = _build_client(cfg, credential_scope="payment")
        day_start = datetime.combine(current.date(), time.min, tzinfo=UTC)
        payments = await client.list_payments(
            creation_date_start=int(day_start.timestamp()),
            creation_date_end=int(current.timestamp()),
        )
    except LightningUnavailableError as exc:
        report["error"] = str(exc)
        return report

    lnd_spent = 0
    for payment in payments:
        status = str(payment.get("status", "")).upper()
        if status not in {"SUCCEEDED", "IN_FLIGHT", "INITIATED"}:
            continue
        try:
            value_sat = int(payment.get("value_sat", 0) or payment.get("value", 0) or 0)
            fee_sat = int(payment.get("fee_sat", 0) or payment.get("fee", 0) or 0)
        except (TypeError, ValueError):
            continue
        lnd_spent += max(0, value_sat) + max(0, fee_sat)
    ledger_spent = int(report["ledger_spent_sat"])
    report.update(
        {
            "lnd_spent_sat": lnd_spent,
            "effective_spent_sat": max(ledger_spent, lnd_spent),
            "gap_sat": lnd_spent - ledger_spent,
            "available": True,
        }
    )
    return report


async def reconcile_open_payments(
    *,
    cfg: LightningSettings,
    path: Path | None = None,
) -> dict[str, Any]:
    """Resolve open ``pay_invoice`` intents through TrackPaymentV2.

    Non-terminal or unreachable states remain open and therefore continue to
    reserve their amount in ``spent_today_sat``.  This is intentionally
    conservative: only LND's explicit SUCCEEDED/FAILED states close an intent.
    """
    report: dict[str, Any] = {
        "checked": 0,
        "succeeded": 0,
        "failed": 0,
        "unresolved": 0,
        "skipped": 0,
        "errors": [],
    }
    if not cfg.enabled:
        report["skipped"] = 1
        return report
    payment_hex, payment_path = cfg.macaroon_credentials("payment")
    if not payment_hex.strip() and not payment_path.strip():
        report["skipped"] = 1
        report["errors"].append("payment macaroon unavailable")
        return report
    try:
        intents = read_open_ln_intents(path)
        client = _build_client(cfg, credential_scope="payment")
    except (LightningOpsLedgerError, LightningUnavailableError) as exc:
        report["errors"].append(str(exc))
        return report

    for intent in intents:
        if intent.get("action") != "pay_invoice":
            report["skipped"] += 1
            continue
        payment_hash = str((intent.get("plan") or {}).get("payment_hash", "")).strip()
        if not payment_hash:
            report["unresolved"] += 1
            report["errors"].append(f"{intent.get('intent_id')}: missing payment_hash")
            continue
        report["checked"] += 1
        try:
            response = await client.track_payment_v2(payment_hash)
        except LightningUnavailableError as exc:
            report["unresolved"] += 1
            report["errors"].append(f"{intent.get('intent_id')}: {exc}")
            continue
        response = {**response, "payment_hash": payment_hash}
        status = str(response.get("status", "")).upper()
        if status == "SUCCEEDED":
            state = "executed"
        elif status == "FAILED":
            state = "error"
        else:
            report["unresolved"] += 1
            continue
        persisted = append_ln_op(
            "pay_invoice",
            state,
            plan=intent.get("plan") or {},
            response=response,
            intent_id=str(intent["intent_id"]),
            path=path,
        )
        if not persisted:
            report["unresolved"] += 1
            report["errors"].append(f"{intent.get('intent_id')}: outcome journal failed")
        elif state == "executed":
            report["succeeded"] += 1
        else:
            report["failed"] += 1

    verification = verify_ln_ops_ledger(path)
    report["ledger_ok"] = verification["ok"]
    report["open_intents"] = verification["open_intents"]
    return report
