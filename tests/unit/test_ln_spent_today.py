"""Daily-Cap-Verdrahtung: spent_today_sat() aus dem Ops-Ledger (Gesamtaudit-P0).

Bisher stand in ln_control:119 hart ``spent_today_sat=0`` — das Tages-Cap der
Policy war damit wirkungslos (die HRF-Zeremonie musste es manuell enforcen).
Semantik: Es zählen nur EXECUTED, wert-abfließende Aktionen (pay_invoice,
keysend, send_coins) des heutigen UTC-Tages. ``open_channel`` bewegt Wert nur
innerhalb der Self-Custody und zählt nicht. Betrag: response-first (tatsächlich
gezahlte Route inkl. Fees), BOLT11-HRP-Fallback, sonst 0 mit Warnung.

Teil 2 deckt ``spent_today_sat_v2`` ab — dieselbe Semantik auf dem redigierten,
verketteten v2-Live-Journal, ZUSÄTZLICH mit Reservierung offener Intents.
``spent_today_sat`` bleibt als eingefrorene v1-Rollback-Semantik unverändert.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.lightning import ops_ledger
from app.lightning.ops_ledger import (
    append_ln_outcome,
    bolt11_amount_sat,
    prepare_ln_intent,
    spent_today_sat,
    spent_today_sat_v2,
)

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


def test_error_spend_counts_conservatively(tmp_path) -> None:
    # Live belegt (25k-Spend 07-02): Client-Timeout loggt "error", die Zahlung
    # settled trotzdem. Fail-closed: Unbekannt zählt gegen das Cap.
    path = tmp_path / "ops.jsonl"
    _write(path, [_rec("pay_invoice", "error", plan={"payment_request": "lnbc250u1pxyz"})])
    assert spent_today_sat(path, now=NOW) == 25000


def test_ignores_non_spends_other_days_and_planned(tmp_path) -> None:
    path = tmp_path / "ops.jsonl"
    _write(
        path,
        [
            _rec("open_channel", "executed", plan={"local_funding_amount": 400000}),
            _rec("open_channel", "error", plan={"local_funding_amount": 400000}),
            _rec("create_invoice", "executed", plan={}),
            _rec("pay_invoice", "planned", plan={"payment_request": "lnbc250u1pxyz"}),
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


# --------------------------------------------------------------------------- #
# Teil 2 — live v2-Cap-Quelle seit PR-C.
# --------------------------------------------------------------------------- #


def test_v2_counts_settled_route_including_fees(tmp_path) -> None:
    p = tmp_path / "ops_v2.jsonl"
    plan = {"payment_request": "lnbc250u1pxyz", "payment_hash": "aa" * 32}
    prepare_ln_intent("pay_invoice", plan=plan, intent_id="i1", path=p, now=NOW)
    append_ln_outcome(
        "pay_invoice",
        "executed",
        plan=plan,
        intent_id="i1",
        response={"payment_route": {"total_amt": "25012", "total_fees": "12"}},
        path=p,
        now=NOW,
    )
    # Intent + Outcome zusammen genau EINMAL, Betrag response-first inkl. Fees.
    assert spent_today_sat_v2(p, now=NOW) == 25012


def test_v2_reserves_open_intent_until_reconciled(tmp_path) -> None:
    # Prozess stirbt nach dem LND-Aufruf, vor dem Outcome-fsync: das Cap darf das
    # Budget nicht erneut freigeben. Fail-closed = der Intent zählt.
    p = tmp_path / "ops_v2.jsonl"
    prepare_ln_intent(
        "pay_invoice",
        plan={"payment_request": "lnbc250u1pxyz", "payment_hash": "bb" * 32},
        intent_id="crashed",
        path=p,
        now=NOW,
    )
    assert spent_today_sat_v2(p, now=NOW) == 25000


def test_v2_ignores_non_spend_actions_and_other_days(tmp_path) -> None:
    p = tmp_path / "ops_v2.jsonl"
    prepare_ln_intent(
        "open_channel", plan={"local_funding_sat": 400_000}, intent_id="oc", path=p, now=NOW
    )
    append_ln_outcome(
        "open_channel",
        "executed",
        plan={"local_funding_sat": 400_000},
        intent_id="oc",
        path=p,
        now=NOW,
    )
    prepare_ln_intent("create_invoice", plan={"value_sat": 1000}, intent_id="ci", path=p, now=NOW)
    prepare_ln_intent(
        "send_coins",
        plan={"amount_sat": 5000, "addr": "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"},
        intent_id="long-past",
        path=p,
        # Outside BOTH windows (calendar day and the m-15 rolling 24 h).
        now=NOW - timedelta(days=2),
    )
    assert spent_today_sat_v2(p, now=NOW) == 0


def test_v2_m15_rolling_window_closes_the_utc_midnight_hop(tmp_path) -> None:
    """m-15: ein Spend kurz vor Mitternacht darf das Cap nicht kurz danach freigeben.

    Kalendertag allein: 23:50 gestern zählt heute NICHT → volles Cap um 00:10
    erneut verfügbar (2× Tagesexposure in 20 Minuten). Das rollende 24-h-Fenster
    hält ihn, bis er wirklich 24 h alt ist; das Maximum aus beiden ist die Quelle.
    """
    p = tmp_path / "ops_v2.jsonl"
    before_midnight = datetime(2026, 7, 1, 23, 50, tzinfo=UTC)
    just_after = datetime(2026, 7, 2, 0, 10, tzinfo=UTC)
    plan = {"amount_sat": 5000, "addr": "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"}
    prepare_ln_intent("send_coins", plan=plan, intent_id="late", path=p, now=before_midnight)
    append_ln_outcome(
        "send_coins", "executed", plan=plan, intent_id="late", path=p, now=before_midnight
    )
    assert spent_today_sat_v2(p, now=just_after) == 5000  # rolling window still holds it
    # ... und fällt erst heraus, wenn er die 24 h überschritten hat.
    assert spent_today_sat_v2(p, now=before_midnight + timedelta(hours=24, minutes=1)) == 0


def test_v2_error_outcome_still_counts(tmp_path) -> None:
    # Live belegt (25k-Spend 07-02): Client-Timeout loggt "error", die Zahlung
    # settled trotzdem. Unbekannt zählt gegen das Cap.
    p = tmp_path / "ops_v2.jsonl"
    plan = {"payment_request": "lnbc250u1pxyz", "payment_hash": "cc" * 32}
    prepare_ln_intent("pay_invoice", plan=plan, intent_id="i1", path=p, now=NOW)
    append_ln_outcome(
        "pay_invoice", "error", plan=plan, intent_id="i1", response={}, path=p, now=NOW
    )
    assert spent_today_sat_v2(p, now=NOW) == 25000


def test_v2_missing_ledger_is_unknown(tmp_path) -> None:
    assert spent_today_sat_v2(tmp_path / "fehlt.jsonl", now=NOW) is None


def test_v2_corrupt_ledger_is_unknown(tmp_path) -> None:
    path = tmp_path / "ops_v2.jsonl"
    path.write_text('{"seq":1', encoding="utf-8")
    assert spent_today_sat_v2(path, now=NOW) is None


def test_v2_hash_chain_corruption_is_unknown(tmp_path) -> None:
    path = tmp_path / "ops_v2.jsonl"
    prepare_ln_intent(
        "send_coins",
        plan={"amount_sat": 5000, "addr": "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"},
        intent_id="tampered",
        path=path,
        now=NOW,
    )
    row = json.loads(path.read_text(encoding="utf-8"))
    row["plan"]["amount_sat"] = 0
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    assert spent_today_sat_v2(path, now=NOW) is None


def test_v2_read_oserror_is_unknown(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "ops_v2.jsonl"
    path.write_text("", encoding="utf-8")

    class _UnreadableLock:
        def __enter__(self) -> object:
            raise OSError("simulated read failure")

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(ops_ledger.portalocker, "Lock", lambda *args, **kwargs: _UnreadableLock())
    assert spent_today_sat_v2(path, now=NOW) is None


def test_v2_existing_empty_fresh_journal_is_known_zero(tmp_path: Path) -> None:
    """A present, chain-valid zero-row migration is known-empty, not missing."""
    path = tmp_path / "freshly_migrated_v2.jsonl"
    path.write_text("", encoding="utf-8")
    assert spent_today_sat_v2(path, now=NOW) == 0
