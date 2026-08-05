"""Tests fuer scripts/c1_payment_branch_eval.py (C1-Zahlungs-Zweig, Prae-Reg 9cab81fae4823482).

Der Evaluator hat am 2026-08-05 das Verdikt FAIL=NO_DEMAND getragen. Ein Evaluator,
der FAIL meldet, ist wertlos, solange nicht gezeigt ist, dass er ueberhaupt PASS
melden KANN — sonst ist "kein Bedarf" von "Skript kaputt" nicht unterscheidbar.
Die Positivkontrolle ist deshalb der wichtigste Test dieser Datei.

Abnahme: Positivkontrolle (PASS moeglich), Fraud-Guard (Selbstzahler kippt nicht
durch), Fenster-/Memo-/Scope-Filter, Divergenzschutz gegen die versiegelte Regel,
und die Reproduktion der real gemessenen 0/0-Lage.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from scripts.c1_payment_branch_eval import (
    MIN_DISTINCT_PAYERS,
    MIN_PAYMENTS,
    evaluate,
    main,
)

WINDOW_START = datetime(2026, 7, 4, 9, 22, 7, tzinfo=UTC)
WINDOW_END = datetime(2026, 8, 3, 23, 59, 59, tzinfo=UTC)

RULE = {
    "prereg_id": "9cab81fae4823482",
    "hypothesis": "oracle_demand_probe_fee_truth_v1",
    "sealed_at_utc": "2026-08-02",
    "sealed_rule": {
        "or_branch": {"status": "NOT_EVALUATED"},
        "payment_branch": {
            "criterion": (
                ">=5 settled L402-Payments mit Memo-Praefix 'kai-oracle:' von "
                ">=3 distinkten Payer-Fingerprints innerhalb des Fensters"
            ),
            "scopes_counted": ["fee-series", "verdicts", "onchain-facts"],
            "source_of_truth": [
                "artifacts/ln_earnings_ledger.jsonl",
                "artifacts/ln_demand_ledger.jsonl",
            ],
            "window": {
                "start_utc": WINDOW_START.isoformat(),
                "end_utc": WINDOW_END.isoformat(),
            },
        },
    },
}


def _payment(payment_hash: str, *, when: datetime, memo: str = "kai-oracle:fee-series") -> dict:
    return {
        "ts": when.isoformat(),
        "payment_hash": payment_hash,
        "amount_sat": 10,
        "source": "oracle-l402",
        "memo": memo,
        "settled_at": str(int(when.timestamp())),
    }


def _challenge(payment_hash: str, fingerprint: str) -> dict:
    return {
        "ts": WINDOW_START.isoformat(),
        "event": "l402_challenge_minted",
        "scope": "fee-series",
        "requester_fp": fingerprint,
        "payment_hash": payment_hash,
    }


def _run(earnings: list[dict], demand: list[dict]) -> dict:
    return evaluate(
        rule=RULE,
        earnings_rows=earnings,
        demand_rows=demand,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
    )


def test_positive_control_five_payments_three_payers_passes() -> None:
    """Die Kern-Positivkontrolle: erfuellte Schwellen MUESSEN PASS ergeben."""
    inside = WINDOW_START.replace(day=10)
    fingerprints = ["fp_a", "fp_b", "fp_c", "fp_a", "fp_b"]
    earnings = [_payment(f"h{i}", when=inside) for i in range(len(fingerprints))]
    demand = [_challenge(f"h{i}", fp) for i, fp in enumerate(fingerprints)]

    result = _run(earnings, demand)

    assert result["verdict"] == "PASS"
    assert result["passed"] is True
    assert result["settled_payments_in_window"] == MIN_PAYMENTS
    assert result["distinct_payer_fps_in_window"] == MIN_DISTINCT_PAYERS
    assert all(check["ok"] for check in result["checks"])


def test_fraud_guard_single_payer_cannot_pass() -> None:
    """Ein Selbstzahler mit 5 Zahlungen scheitert an der Fingerprint-Schwelle."""
    inside = WINDOW_START.replace(day=10)
    earnings = [_payment(f"h{i}", when=inside) for i in range(5)]
    demand = [_challenge(f"h{i}", "fp_solo") for i in range(5)]

    result = _run(earnings, demand)

    assert result["verdict"] == "FAIL"
    assert result["settled_payments_in_window"] == 5
    assert result["distinct_payer_fps_in_window"] == 1
    payer_check = next(c for c in result["checks"] if c["name"] == "distinct_payer_fingerprints")
    assert payer_check["ok"] is False


def test_payments_before_window_start_are_excluded() -> None:
    """Die zwei realen 02.07.-Zahlungen liegen vor dem Fenster und zaehlen nicht."""
    before = datetime(2026, 7, 2, 6, 1, 47, tzinfo=UTC)
    earnings = [_payment(f"h{i}", when=before, memo="kai-oracle:onchain-facts") for i in range(5)]
    demand = [_challenge(f"h{i}", f"fp_{i}") for i in range(5)]

    result = _run(earnings, demand)

    assert result["verdict"] == "FAIL"
    assert result["settled_payments_in_window"] == 0
    assert all(
        "settled ausserhalb des Fensters" in row["excluded_because"]
        for row in result["earnings_rows_considered"]
    )


def test_non_oracle_memo_is_excluded() -> None:
    """Die 25k-sat lnurlp-Zeile traegt keinen kai-oracle:-Praefix und zaehlt nie mit."""
    inside = WINDOW_START.replace(day=10)
    earnings = [_payment("h0", when=inside, memo="kai-pay: KAI receive")]

    result = _run(earnings, [_challenge("h0", "fp_a")])

    assert result["settled_payments_in_window"] == 0
    assert (
        "memo trägt keinen kai-oracle:-Präfix"
        in (result["earnings_rows_considered"][0]["excluded_because"])
    )


def test_scope_outside_sealed_list_is_excluded() -> None:
    """Nur die drei versiegelten Scopes zaehlen."""
    inside = WINDOW_START.replace(day=10)
    earnings = [_payment("h0", when=inside, memo="kai-oracle:some-other-scope")]

    result = _run(earnings, [_challenge("h0", "fp_a")])

    assert result["settled_payments_in_window"] == 0
    reasons = result["earnings_rows_considered"][0]["excluded_because"]
    assert any("nicht in gezählten Scopes" in reason for reason in reasons)


def test_settled_at_beats_booking_ts() -> None:
    """Massgeblich ist settled_at, nicht der Buchungszeitpunkt ts."""
    inside = WINDOW_START.replace(day=10)
    row = _payment("h0", when=inside)
    row["ts"] = "2026-07-02T06:16:51+00:00"  # Buchung vor dem Fenster

    result = _run([row], [_challenge("h0", "fp_a")])

    assert result["settled_payments_in_window"] == 1
    assert result["earnings_rows_considered"][0]["settle_time_source"] == "settled_at"


def test_divergence_guard_rejects_rule_without_the_sealed_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Weicht der Regeltext von den Skript-Schwellen ab, bricht der Lauf ab."""
    tampered = json.loads(json.dumps(RULE))
    tampered["sealed_rule"]["payment_branch"]["criterion"] = (
        ">=2 settled L402-Payments von >=1 distinkten Payer-Fingerprints"
    )
    rule_path = tmp_path / "rule.json"
    rule_path.write_text(json.dumps(tampered), encoding="utf-8")

    monkeypatch.setattr(
        "sys.argv", ["c1_payment_branch_eval.py", "--rule", str(rule_path), "--json"]
    )

    with pytest.raises(SystemExit) as excinfo:
        main()

    assert "divergiert" in str(excinfo.value)


def test_real_ledger_shape_reproduces_zero_zero() -> None:
    """Die real gemessene Lage: 2 Oracle-Zahlungen vor dem Fenster, 1 Nicht-Oracle-Zahlung."""
    earnings = [
        _payment(
            "3baf314f",
            when=datetime(2026, 7, 2, 6, 1, 47, tzinfo=UTC),
            memo="kai-oracle:onchain-facts",
        ),
        _payment(
            "973358cf",
            when=datetime(2026, 7, 2, 6, 14, 28, tzinfo=UTC),
            memo="kai-oracle:onchain-facts",
        ),
        _payment(
            "d88cfb62",
            when=datetime(2026, 7, 4, 8, 52, 39, tzinfo=UTC),
            memo="kai-pay: KAI receive",
        ),
    ]
    demand = [_challenge("3baf314f", "beed052613f160c5")]

    result = _run(earnings, demand)

    assert result["verdict"] == "FAIL"
    assert result["settled_payments_in_window"] == 0
    assert result["distinct_payer_fps_in_window"] == 0
    assert result["qualifying_payments"] == []
