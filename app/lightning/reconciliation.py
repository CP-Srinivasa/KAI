"""Crash-gap reconciliation for the Lightning v2 money journal.

The reconciler is deliberately not a payment path.  It uses the read macaroon,
compares already-durable open intents with lnd's outgoing-payment history, and
may append only a terminal journal outcome.  It never creates an intent, sends a
payment, retries one, or changes a feature flag.

Order matters: verified journal snapshot -> verified Truth tip containment ->
complete paginated node scan -> repeat both local checks -> terminal appends.
Any uncertainty before the last step leaves every intent open.
"""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from app.core.file_lock import append_lock
from app.lightning.client import LndPayment, LndPaymentPage
from app.lightning.ops_ledger import (
    append_ln_outcome,
    ln_ops_v2_path,
    read_verified_ln_ops_snapshot,
)
from app.truth.ledger import DEFAULT_TRUTH_LEDGER_PATH, read_verified_ledger

DEFAULT_RECONCILIATION_REPORT_PATH = Path("artifacts/lightning/ln_reconciliation.jsonl")
SCHEMA = "ln-reconciliation/v1"
_HEX_32 = re.compile(r"^[0-9a-f]{64}$")
_TERMINAL_PAYMENT_STATUSES = frozenset({"SUCCEEDED", "FAILED"})
_SAFE_FAILURE_REASONS = frozenset(
    {
        "FAILURE_REASON_NONE",
        "FAILURE_REASON_TIMEOUT",
        "FAILURE_REASON_NO_ROUTE",
        "FAILURE_REASON_ERROR",
        "FAILURE_REASON_INCORRECT_PAYMENT_DETAILS",
        "FAILURE_REASON_INSUFFICIENT_BALANCE",
        "FAILURE_REASON_CANCELED",
    }
)


class PaymentsReader(Protocol):
    async def list_payments(
        self,
        *,
        include_incomplete: bool = True,
        index_offset: int = 0,
        max_payments: int = 1000,
        reversed: bool = False,
        omit_hops: bool = True,
    ) -> LndPaymentPage: ...


def _tip_cross_check(*, journal_records: list[dict[str, Any]], truth_path: Path) -> dict[str, Any]:
    """Prove that the latest attested LN tip still occurs in this journal."""
    result: dict[str, Any] = {
        "contained": False,
        "truth_seq": None,
        "journal_seq": None,
        "reason": "unknown",
    }
    truth = read_verified_ledger(truth_path)
    if not truth["ok"]:
        result["reason"] = "truth_ledger_invalid"
        return result
    attestations = [row for row in truth["records"] if row.get("kind") == "lightning_ops_tip"]
    if not attestations:
        result["reason"] = "truth_tip_missing"
        return result

    latest = attestations[-1]
    result["truth_seq"] = latest.get("seq")
    payload = latest.get("payload")
    subject = str(latest.get("subject_id") or "")
    if not isinstance(payload, dict) or not subject.startswith("ln-ops-tip:"):
        result["reason"] = "truth_tip_malformed"
        return result
    subject_hash = subject.removeprefix("ln-ops-tip:").lower()
    payload_hash = str(payload.get("record_hash") or "").lower()
    raw_payload_seq = payload.get("seq")
    try:
        if not isinstance(raw_payload_seq, (str, int)) or isinstance(raw_payload_seq, bool):
            raise TypeError
        payload_seq = int(raw_payload_seq)
    except (TypeError, ValueError):
        result["reason"] = "truth_tip_malformed"
        return result
    if (
        not _HEX_32.fullmatch(subject_hash)
        or payload_hash != subject_hash
        or payload.get("schema") != "ln-ops-tip/v1"
        or payload_seq <= 0
    ):
        result["reason"] = "truth_tip_malformed"
        return result

    matched = next(
        (
            row
            for row in journal_records
            if str(row.get("record_hash") or "").lower() == subject_hash
            and row.get("seq") == payload_seq
        ),
        None,
    )
    if matched is None:
        result["reason"] = "attested_tip_not_in_journal"
        return result
    result.update(
        {
            "contained": True,
            "journal_seq": payload_seq,
            "reason": "contained",
        }
    )
    return result


def _open_intent_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    open_ids = set(snapshot["open_intents"])
    return [
        row
        for row in snapshot["records"]
        if row.get("state") == "intent" and str(row.get("intent_id") or "") in open_ids
    ]


def _safe_failure_reason(reason: str) -> str:
    normalized = reason.strip().upper()
    if not normalized:
        return "FAILURE_REASON_NONE"
    return normalized if normalized in _SAFE_FAILURE_REASONS else "FAILURE_REASON_UNRECOGNIZED"


async def _scan_all_payments(
    client: PaymentsReader,
    *,
    page_size: int,
    max_pages: int,
) -> tuple[list[LndPayment], int]:
    """Return a complete forward scan or raise without exposing a partial result."""
    if page_size <= 0 or max_pages <= 0:
        raise ValueError("page_size and max_pages must be > 0")
    offset = 0
    payments: list[LndPayment] = []
    for page_number in range(1, max_pages + 1):
        page = await client.list_payments(
            include_incomplete=True,
            index_offset=offset,
            max_payments=page_size,
            reversed=False,
            omit_hops=True,
        )
        payments.extend(page.payments)
        if len(page.payments) < page_size:
            return payments, page_number
        next_offset = page.next_index_offset
        if next_offset <= offset:
            raise RuntimeError("list_payments pagination did not advance")
        offset = next_offset
    raise RuntimeError("list_payments exceeded the page safety limit")


def _base_report(moment: datetime) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "ts": moment.astimezone(UTC).isoformat(),
        "status": "error",
        "tip_cross_check": {
            "contained": False,
            "truth_seq": None,
            "journal_seq": None,
            "reason": "not_checked",
        },
        "journal": {
            "records_before": 0,
            "open_before": 0,
            "records_after": 0,
            "open_after": 0,
        },
        "node": {"pages": 0, "payments": 0, "skipped": None},
        "intents": [],
        "errors": [],
    }


def _append_report(report: dict[str, Any], path: Path) -> None:
    """Append one fsync'd, single-line, secret-free reconciliation result."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with append_lock(path, strict=True):
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())


def _finish(report: dict[str, Any], path: Path) -> dict[str, Any]:
    _append_report(report, path)
    return report


async def reconcile_ln_ops(
    *,
    client: PaymentsReader | None,
    ops_path: Path | None = None,
    truth_path: Path = DEFAULT_TRUTH_LEDGER_PATH,
    report_path: Path = DEFAULT_RECONCILIATION_REPORT_PATH,
    page_size: int = 1000,
    max_pages: int = 10_000,
    now: datetime | None = None,
    client_error: str = "",
) -> dict[str, Any]:
    """Reconcile open BOLT11 intents against lnd without ever initiating value flow."""
    moment = now or datetime.now(UTC)
    report = _base_report(moment)
    target = ops_path or ln_ops_v2_path()
    before = read_verified_ln_ops_snapshot(target)
    if not before["ok"]:
        report["errors"].append("money_journal_invalid")
        return _finish(report, report_path)
    report["journal"]["records_before"] = before["checked"]
    report["journal"]["open_before"] = len(before["open_intents"])
    report["journal"]["records_after"] = before["checked"]
    report["journal"]["open_after"] = len(before["open_intents"])

    cross_check = _tip_cross_check(journal_records=before["records"], truth_path=truth_path)
    report["tip_cross_check"] = cross_check
    if not cross_check["contained"]:
        report["errors"].append(str(cross_check["reason"]))
        return _finish(report, report_path)

    open_rows = _open_intent_rows(before)
    if not open_rows:
        report["node"]["skipped"] = "no_open_intents"
        report["status"] = "ok"
        return _finish(report, report_path)

    supported = [row for row in open_rows if row.get("action") == "pay_invoice"]
    unsupported = [row for row in open_rows if row.get("action") != "pay_invoice"]
    for row in unsupported:
        report["intents"].append(
            {
                "intent_id": str(row.get("intent_id") or ""),
                "action": str(row.get("action") or ""),
                "payment_hash": "",
                "node_status": "",
                "result": "left_open",
                "reason": "unsupported_action",
            }
        )
    if not supported:
        report["node"]["skipped"] = "no_supported_open_intents"
        report["status"] = "attention"
        return _finish(report, report_path)
    if client is None:
        report["errors"].append(client_error or "read_client_unavailable")
        return _finish(report, report_path)

    try:
        payments, pages = await _scan_all_payments(client, page_size=page_size, max_pages=max_pages)
    except Exception as exc:  # noqa: BLE001 — partial scans may never escape
        report["errors"].append(f"node_scan_failed:{type(exc).__name__}")
        return _finish(report, report_path)
    report["node"].update({"pages": pages, "payments": len(payments), "skipped": None})

    # The scan may take time. Re-read and re-prove containment before ANY append;
    # this closes the truncation/concurrent-outcome window between the first proof
    # and the node response.
    current = read_verified_ln_ops_snapshot(target)
    if not current["ok"]:
        report["errors"].append("money_journal_changed_invalid")
        return _finish(report, report_path)
    second_cross_check = _tip_cross_check(journal_records=current["records"], truth_path=truth_path)
    report["tip_cross_check"] = second_cross_check
    if not second_cross_check["contained"]:
        report["errors"].append(str(second_cross_check["reason"]))
        return _finish(report, report_path)

    current_open = set(current["open_intents"])
    by_hash: dict[str, list[LndPayment]] = defaultdict(list)
    for payment in payments:
        by_hash[payment.payment_hash].append(payment)

    for row in supported:
        intent_id = str(row.get("intent_id") or "")
        raw_plan = row.get("plan")
        plan: dict[str, Any] = raw_plan if isinstance(raw_plan, dict) else {}
        payment_hash = str(plan.get("payment_hash") or "").lower()
        item: dict[str, Any] = {
            "intent_id": intent_id,
            "action": "pay_invoice",
            "payment_hash": payment_hash if _HEX_32.fullmatch(payment_hash) else "",
            "node_status": "",
            "result": "left_open",
            "reason": "",
        }
        report["intents"].append(item)
        if intent_id not in current_open:
            item.update(result="already_terminal_concurrent", reason="concurrent_outcome")
            continue
        if not _HEX_32.fullmatch(payment_hash):
            item["reason"] = "invalid_intent_payment_hash"
            continue
        matches = by_hash.get(payment_hash, [])
        if not matches:
            item["reason"] = "payment_not_found"
            continue
        if len(matches) != 1:
            item["reason"] = "ambiguous_payment_hash"
            continue
        payment = matches[0]
        item["node_status"] = payment.status
        try:
            amount_sat = int(plan.get("amount_sat") or 0)
        except (TypeError, ValueError):
            amount_sat = 0
        if amount_sat <= 0 or payment.value_sat != amount_sat:
            item["reason"] = "amount_mismatch"
            continue
        if payment.status not in _TERMINAL_PAYMENT_STATUSES:
            item["reason"] = "node_payment_nonterminal"
            continue
        response = {
            "status": payment.status,
            "payment_hash": payment.payment_hash,
            "amount_sat": payment.value_sat,
            "fee_sat": payment.fee_sat,
        }
        state = "executed"
        if payment.status == "FAILED":
            state = "error"
            response["failure_reason"] = _safe_failure_reason(payment.failure_reason)
        journalled = append_ln_outcome(
            "pay_invoice",
            state,
            intent_id=intent_id,
            plan=plan,
            response=response,
            path=target,
            now=moment,
        )
        if journalled:
            item.update(result=f"journalled_{state}", reason="node_terminal_match")
        else:
            item.update(result="append_unproven", reason="journal_append_failed")

    after = read_verified_ln_ops_snapshot(target)
    if not after["ok"]:
        report["errors"].append("money_journal_post_verify_failed")
        return _finish(report, report_path)
    report["journal"]["records_after"] = after["checked"]
    report["journal"]["open_after"] = len(after["open_intents"])
    if any(item["result"] == "append_unproven" for item in report["intents"]):
        report["errors"].append("journal_append_unproven")
    if report["errors"]:
        report["status"] = "error"
    elif after["open_intents"]:
        report["status"] = "attention"
    else:
        report["status"] = "ok"
    return _finish(report, report_path)


__all__ = [
    "DEFAULT_RECONCILIATION_REPORT_PATH",
    "SCHEMA",
    "PaymentsReader",
    "reconcile_ln_ops",
]
