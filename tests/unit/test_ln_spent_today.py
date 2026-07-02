"""Daily-Cap-Verdrahtung: spent_today_sat() aus dem Ops-Ledger (Gesamtaudit-P0).

Bisher stand in ln_control:119 hart ``spent_today_sat=0`` — das Tages-Cap der
Policy war damit wirkungslos (die HRF-Zeremonie musste es manuell enforcen).
Semantik: Es zählen nur EXECUTED, wert-abfließende Aktionen (pay_invoice,
keysend, send_coins) des heutigen UTC-Tages. ``open_channel`` bewegt Wert nur
innerhalb der Self-Custody und zählt nicht. Betrag: response-first (tatsächlich
gezahlte Route inkl. Fees), BOLT11-HRP-Fallback, sonst 0 mit Warnung.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app.lightning.ops_ledger import bolt11_amount_sat, spent_today_sat

NOW = datetime(2026, 7, 2, 12, 0, 0, tzinfo=UTC)


def _write(path, records) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


def _rec(action, state, *, ts="2026-07-02T08:00:00+00:00", plan=None, response=None):
    return {
        "ts": ts,
        "action": action,
        "state": state,
        "plan": plan or {},
        "response": response or {},
    }


def test_bolt11_hrp_amounts() -> None:
    assert bolt11_amount_sat("lnbc250u1pxyz") == 25000
    assert bolt11_amount_sat("lnbc21u1pxyz") == 2100
    assert bolt11_amount_sat("lnbc1m1pxyz") == 100000
    assert bolt11_amount_sat("lnbc100n1pxyz") == 10
    assert bolt11_amount_sat("lnbc1pxyz") == 0  # amountless -> unbekannt
    assert bolt11_amount_sat("kein-invoice") == 0


def test_counts_executed_spends_today(tmp_path) -> None:
    path = tmp_path / "ops.jsonl"
    _write(
        path,
        [
            _rec(
                "pay_invoice",
                "executed",
                plan={"payment_request": "lnbc250u1pxyz"},
                response={"payment_route": {"total_amt": "25012"}},
            ),
            _rec("keysend", "executed", plan={"amt_sat": 500}),
            _rec("send_coins", "executed", plan={"amount_sat": 1000}),
        ],
    )
    # response-first (25012 inkl. Fees) + keysend 500 + send_coins 1000
    assert spent_today_sat(path, now=NOW) == 26512


def test_bolt11_fallback_when_no_route_response(tmp_path) -> None:
    path = tmp_path / "ops.jsonl"
    _write(path, [_rec("pay_invoice", "executed", plan={"payment_request": "lnbc21u1pxyz"})])
    assert spent_today_sat(path, now=NOW) == 2100


def test_ignores_non_spends_other_days_and_non_executed(tmp_path) -> None:
    path = tmp_path / "ops.jsonl"
    _write(
        path,
        [
            _rec("open_channel", "executed", plan={"local_funding_amount": 400000}),
            _rec("create_invoice", "executed", plan={}),
            _rec("pay_invoice", "planned", plan={"payment_request": "lnbc250u1pxyz"}),
            _rec("pay_invoice", "error", plan={"payment_request": "lnbc250u1pxyz"}),
            _rec(
                "pay_invoice",
                "executed",
                ts="2026-07-01T23:59:59+00:00",  # gestern (UTC)
                plan={"payment_request": "lnbc250u1pxyz"},
            ),
        ],
    )
    assert spent_today_sat(path, now=NOW) == 0


def test_missing_ledger_is_zero(tmp_path) -> None:
    assert spent_today_sat(tmp_path / "fehlt.jsonl", now=NOW) == 0
