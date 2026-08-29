"""Resolve every money-journal action against node evidence (G2 pre-registration).

The pre-registered question is *"under which conditions can a sealed record be
false, and what mechanism prevents it?"* — and the forensics already named the
condition: **sealing proves immutability, never truth.** The hash chain protects
against later change; it says nothing about whether the value was right when it
was taken. So the mechanism has to work on both sides of the seal: a structural
guard before it (:mod:`app.lightning.plan_guards`) and this resolver after it.

Deliberately PURE. It takes the journal rows and a node snapshot and returns a
verdict per action; it opens no socket. That is what makes the pre-registration
evaluable at all: the same inputs must always produce the same verdicts, a
positive control must show it recognises the real actions, and a negative control
must show that changing the node evidence CHANGES the verdict — otherwise the
resolver is not measuring the node, it is guessing.

**What v2 alone cannot tell you.** The v2 rows are redacted: an ``open_channel``
plan carries ``peer_hash``, never the pubkey it was hashed from. The fixture value
``02ab`` is therefore invisible in v2, and no structural guard can find it there.
Two consequences, both honest rather than convenient:

  * a fixture row whose state is ``executed`` is still catchable — the node has no
    matching movement, and *journal says executed while the node says nothing* is a
    contradiction in its own right (``CONTRADICTED``);
  * a fixture row whose state is ``error`` is **indistinguishable** from a genuine
    failure using node evidence alone. Nothing moved either way. It is only
    identifiable from the unredacted legacy v1 plan, which callers may pass in.

Verdicts:

  ``EXECUTED_CONFIRMED``      node evidence shows value moved
  ``NOT_EXECUTED_CONFIRMED``  node evidence shows value did not move
  ``CONTRADICTED``            the row claims ``executed``; the node shows nothing
  ``NOT_REAL``                the (legacy) plan cannot describe any real action
  ``UNRESOLVED``              the evidence does not decide it — never a guess
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.lightning.plan_guards import plan_structural_defects

EXECUTED_CONFIRMED = "EXECUTED_CONFIRMED"
NOT_EXECUTED_CONFIRMED = "NOT_EXECUTED_CONFIRMED"
CONTRADICTED = "CONTRADICTED"
NOT_REAL = "NOT_REAL"
UNRESOLVED = "UNRESOLVED"

#: How far apart a journal timestamp and a node timestamp may be and still count
#: as the same event. The v1 writer wrote AFTER the call, so the journal runs up
#: to 10 s late; a tight window would reject true matches. A wide one would merge
#: distinct payments of equal size — the two 25.000 sat payments of 2026-07-02 and
#: 07-03 lie 33 hours apart, so 120 s is far from either failure mode.
MATCH_WINDOW_S = 120

#: An on-chain open debits funding + miner fee. The observed fee was 308 sat; the
#: allowance is generous enough for a fee spike and far below any plausible second
#: movement.
MAX_OPEN_FEE_SAT = 10_000


def _plan_amount(record: dict[str, Any]) -> int:
    plan = record.get("plan") or {}
    for key in ("amount_sat", "local_funding_sat", "amt_sat"):
        try:
            value = int(plan.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0


def _iso_to_unix(text: str) -> int | None:
    try:
        return int(datetime.fromisoformat(str(text)).timestamp())
    except (TypeError, ValueError):
        return None


def _resolve_pay_invoice(
    record: dict[str, Any], node: dict[str, Any]
) -> tuple[str, str, list[str]]:
    payments = node.get("payments")
    if payments is None:
        return UNRESOLVED, "no payment evidence supplied", []
    amount = _plan_amount(record)
    when = _iso_to_unix(record.get("ts", ""))
    for payment in payments:
        try:
            value = int(payment.get("value_sat") or 0)
            created = int(payment.get("creation_date") or 0)
        except (TypeError, ValueError):
            continue
        if value != amount:
            continue
        if when is not None and abs(created - when) > MATCH_WINDOW_S:
            continue
        status = str(payment.get("status", ""))
        evidence = [str(payment.get("payment_hash", ""))]
        if status == "SUCCEEDED":
            return EXECUTED_CONFIRMED, f"node payment SUCCEEDED for {value} sat", evidence
        if status == "FAILED":
            return NOT_EXECUTED_CONFIRMED, f"node payment FAILED for {value} sat", evidence
    return NOT_EXECUTED_CONFIRMED, f"no node payment of {amount} sat in the window", []


def _resolve_onchain(record: dict[str, Any], node: dict[str, Any]) -> tuple[str, str, list[str]]:
    """On-chain actions are decided by the WALLET, not by the channel list.

    A channel opened and later closed is gone from ``channels`` — reading only that
    list would call a real, since-closed open "never happened". The wallet debit is
    the fact that survives: it cannot be undone by a later close.
    """
    debits = node.get("wallet_debits")
    if debits is None:
        return UNRESOLVED, "no wallet movement evidence supplied", []
    amount = _plan_amount(record)
    when = _iso_to_unix(record.get("ts", ""))
    for move in debits:
        try:
            debit = int(move.get("amount_sat") or 0)
            at = int(move.get("unix") or 0)
        except (TypeError, ValueError):
            continue
        if not (amount <= debit <= amount + MAX_OPEN_FEE_SAT):
            continue
        if when is not None and not (when <= at <= when + 24 * 3600):
            continue  # a debit before the row cannot have been caused by it
        return (
            EXECUTED_CONFIRMED,
            f"wallet fell by {debit} sat after the row ({amount} + fee)",
            [str(move.get("evidence", ""))],
        )
    return NOT_EXECUTED_CONFIRMED, f"no wallet debit matching {amount} sat follows the row", []


def resolve_record(
    record: dict[str, Any],
    node: dict[str, Any],
    *,
    legacy_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve one money-journal row; ``legacy_plan`` is the unredacted v1 plan."""
    action = str(record.get("action", ""))
    state = str(record.get("state", ""))
    seq = int(record.get("seq", 0))

    if legacy_plan is not None:
        defects = plan_structural_defects(action, legacy_plan)
        if defects:
            return {
                "seq": seq,
                "action": action,
                "journal_state": state,
                "verdict": NOT_REAL,
                "reason": "; ".join(defects),
                "node_evidence": [],
            }

    if action == "pay_invoice":
        verdict, reason, evidence = _resolve_pay_invoice(record, node)
    elif action in ("open_channel", "send_coins"):
        verdict, reason, evidence = _resolve_onchain(record, node)
    else:
        verdict, reason, evidence = UNRESOLVED, f"no rule for action {action!r}", []

    # A row that claims it happened while the node shows nothing is not merely
    # "not executed" — the journal and the node disagree, and that is the finding.
    if verdict == NOT_EXECUTED_CONFIRMED and state == "executed":
        verdict = CONTRADICTED
        reason = f"journal says executed, but {reason}"

    return {
        "seq": seq,
        "action": action,
        "journal_state": state,
        "verdict": verdict,
        "reason": reason,
        "node_evidence": evidence,
    }


def resolve_journal(
    records: list[dict[str, Any]],
    node: dict[str, Any],
    *,
    legacy_plans: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resolve every intent row and report the decomposition, never a bare total.

    Only ``intent`` rows are resolved: an intent and its outcome are one action, so
    counting both would double every figure. The intent's own state is not the
    subject — the state under test is the action's TERMINAL state, which is why the
    outcome row's state is carried over before resolving.
    """
    terminal_by_intent: dict[str, str] = {}
    for record in records:
        state = str(record.get("state", ""))
        if state in ("executed", "error"):
            terminal_by_intent[str(record.get("intent_id", ""))] = state

    resolutions: list[dict[str, Any]] = []
    for record in records:
        if str(record.get("state", "")) != "intent":
            continue
        merged = dict(record)
        merged["state"] = terminal_by_intent.get(str(record.get("intent_id", "")), "intent")
        seq = int(record.get("seq", 0))
        resolutions.append(resolve_record(merged, node, legacy_plan=(legacy_plans or {}).get(seq)))

    by_verdict: dict[str, int] = {}
    for item in resolutions:
        by_verdict[item["verdict"]] = by_verdict.get(item["verdict"], 0) + 1
    disagreements = [
        item
        for item in resolutions
        if item["verdict"] in (CONTRADICTED, NOT_REAL)
        or (item["verdict"] == EXECUTED_CONFIRMED and item["journal_state"] != "executed")
    ]
    return {
        "n_actions": len(resolutions),
        "by_verdict": by_verdict,
        "unresolved": by_verdict.get(UNRESOLVED, 0),
        "disagreements": disagreements,
        "resolutions": resolutions,
    }


__all__ = [
    "CONTRADICTED",
    "EXECUTED_CONFIRMED",
    "MATCH_WINDOW_S",
    "NOT_EXECUTED_CONFIRMED",
    "NOT_REAL",
    "UNRESOLVED",
    "resolve_journal",
    "resolve_record",
]
