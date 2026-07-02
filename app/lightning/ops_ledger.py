"""Lightning value-layer operations ledger (tamper-evident audit trail).

The append-only ``artifacts/ln_ops_ledger.jsonl`` records every node-touching
value-layer action (plan + outcome) for an L3-OTS-anchorable audit trail. Read side
feeds the dashboard; write side (Sprint 4) is called by the gated value layer on
every executed/error outcome. No capital path of its own.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.lightning.jsonl_tail import read_recent_jsonl

logger = logging.getLogger(__name__)

_OPS_PATH = Path("artifacts/ln_ops_ledger.jsonl")


def read_recent_ln_ops(path: Path | None = None, *, limit: int = 200) -> list[dict[str, Any]]:
    """Read the most recent value-layer ops (newest last); ``[]`` until the gated
    writer produces any. Tolerant: missing file / blank / corrupt lines skipped."""
    return read_recent_jsonl(path or _OPS_PATH, limit=limit)


def append_ln_op(
    action: str,
    state: str,
    *,
    plan: dict[str, Any],
    response: dict[str, Any] | None = None,
    path: Path | None = None,
) -> None:
    """Append one value-layer op (plan + outcome) to the audit ledger.

    Fail-soft: a write error is logged and swallowed — the audit trail must NEVER
    kill the (already-gated) send path. Append-only JSONL, one line per op.
    """
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "action": action,
        "state": state,
        "plan": plan,
        "response": response or {},
    }
    out = path or _OPS_PATH
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001 — audit must never kill the send path
        logger.warning("[ln-ops] append failed: %s", exc)


# --------------------------------------------------------------------------- #
# Daily-Cap-Quelle (Gesamtaudit-P0): Wert-ABFLIESSENDE Aktionen des UTC-Tages.
# ``open_channel``/``close_channel`` bewegen Wert nur innerhalb der Self-Custody
# und zählen bewusst NICHT — sonst würde jede Channel-Eröffnung das Tages-Cap
# für echte Sends blockieren, obwohl kein Sat den Operator verlässt.
# --------------------------------------------------------------------------- #

SPEND_ACTIONS = frozenset({"pay_invoice", "keysend", "send_coins"})

_BOLT11_HRP_RE = re.compile(r"^ln(?:bc|tb|bcrt)(\d+)([munp]?)1", re.IGNORECASE)
_HRP_MULTIPLIER_MSAT_PER_UNIT = {
    # msat pro HRP-Einheit: 1 BTC = 1e11 msat; m=1e-3, u=1e-6, n=1e-9, p=1e-12 BTC
    "": 100_000_000_000,
    "m": 100_000_000,
    "u": 100_000,
    "n": 100,
    "p": 0,  # Pico unter msat-Granularität nur bei nicht-10er-Vielfachen; s. unten
}


def bolt11_amount_sat(payment_request: str) -> int:
    """Betrag (sat) aus dem BOLT11-HRP — 0 wenn amountless/unparsebar.

    Konservativ aufgerundet (ein Cap darf nie durch Abrunden unterlaufen werden).
    """
    match = _BOLT11_HRP_RE.match(payment_request.strip())
    if not match:
        return 0
    digits, unit = int(match.group(1)), match.group(2).lower()
    if unit == "p":
        msat = -(-digits * 100 // 1000)  # p: 1e-12 BTC = 0.1 msat -> ceil auf msat
    else:
        msat = digits * _HRP_MULTIPLIER_MSAT_PER_UNIT[unit]
    return -(-msat // 1000)  # ceil msat -> sat


def _spend_amount_sat(record: dict[str, Any]) -> int:
    """Tatsächlich abgeflossene sat eines executed Spends (response-first)."""
    response = record.get("response") or {}
    route = response.get("payment_route") or {}
    try:
        total_amt = int(route.get("total_amt", 0) or 0)  # inkl. Routing-Fees
    except (TypeError, ValueError):
        total_amt = 0
    if total_amt > 0:
        return total_amt
    plan = record.get("plan") or {}
    action = record.get("action")
    if action == "pay_invoice":
        amount = bolt11_amount_sat(str(plan.get("payment_request", "")))
        if amount == 0:
            logger.warning("[ln-ops] spend amount unknown (amountless invoice?): %s", action)
        return amount
    try:
        return int(plan.get("amt_sat") or plan.get("amount_sat") or 0)
    except (TypeError, ValueError):
        return 0


def spent_today_sat(path: Path | None = None, *, now: datetime | None = None) -> int:
    """Summe der heute (UTC) wert-abfließenden Sends — Daily-Cap-Quelle (fail-closed).

    Zählt ``executed`` UND ``error``: ein error-Record kann ein real settled Spend
    sein (Client-Timeout NACH dem Senden — live belegt durch den 25k-Spend vom
    07-02, error geloggt, Channel-Balancen beweisen Settlement). Für ein
    Sicherheits-Cap gilt: Unbekannt = mitzählen; ein echter Fehlschlag over-counted
    dann nur Richtung needs_confirm. ``planned``/``disabled`` berühren den Node nie.
    Tolerant gegen fehlende Datei/korrupte Zeilen.
    """
    today = (now or datetime.now(UTC)).date()
    total = 0
    for record in read_recent_jsonl(path or _OPS_PATH, limit=2000):
        if record.get("state") not in ("executed", "error"):
            continue
        if record.get("action") not in SPEND_ACTIONS:
            continue
        try:
            ts = datetime.fromisoformat(str(record.get("ts", "")))
        except ValueError:
            continue
        if ts.astimezone(UTC).date() != today:
            continue
        total += _spend_amount_sat(record)
    return total
