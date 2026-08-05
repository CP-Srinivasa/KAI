"""Der C1-Zahlungs-Zweig muss rechnen, nicht behaupten.

Das Verdikt zu ``9cab81fae4823482`` entscheidet über Fork-B, ADR 0016 und den
Start von Welle 0. Es darf deshalb nicht aus einer Sichtprüfung des Ledgers
stammen — genau diese Lücke (``gate=null``, kein Evaluator) war der Befund
C1-DEF-1/-3 der versiegelten Auswertungsvorschrift.

Getestet wird die reine Rechenfunktion gegen die vier Ausschlussgründe
(Fenster, Memo-Präfix, Scope, fehlender Zeitstempel), beide Schwellen einzeln
und den Zeitstempel-Parser: ``settled_at`` kommt im echten Ledger als
Unix-Sekunden-STRING, ``ts`` als ISO — wer nur eines davon versteht, zählt
falsch.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from scripts.c1_payment_branch_eval import (
    MIN_DISTINCT_PAYERS,
    MIN_PAYMENTS,
    _parse_ts,
    _payer_index,
    evaluate,
    main,
)

WINDOW_START = datetime(2026, 7, 4, 9, 22, 7, tzinfo=UTC)
WINDOW_END = datetime(2026, 8, 3, 23, 59, 59, tzinfo=UTC)

RULE: dict[str, Any] = {
    "sealed_rule": {
        "payment_branch": {"scopes_counted": ["fee-series", "verdicts", "onchain-facts"]}
    }
}


def _payment(
    payment_hash: str,
    *,
    settled: str = "2026-07-10T12:00:00+00:00",
    memo: str = "kai-oracle:fee-series",
) -> dict[str, Any]:
    return {
        "ts": settled,
        "payment_hash": payment_hash,
        "amount_sat": 10,
        "source": "oracle-l402",
        "memo": memo,
        "settled_at": settled,
    }


def _challenge(payment_hash: str, fingerprint: str) -> dict[str, Any]:
    return {
        "ts": "2026-07-10T11:59:00+00:00",
        "event": "l402_challenge_minted",
        "scope": "fee-series",
        "requester_fp": fingerprint,
        "payment_hash": payment_hash,
    }


def _run(earnings: list[dict[str, Any]], demand: list[dict[str, Any]]) -> dict[str, Any]:
    return evaluate(
        rule=RULE,
        earnings_rows=earnings,
        demand_rows=demand,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
    )


def test_leerer_ledger_ist_fail_und_nicht_pass_by_default() -> None:
    result = _run([], [])
    assert result["verdict"] == "FAIL"
    assert result["settled_payments_in_window"] == 0
    assert result["distinct_payer_fps_in_window"] == 0


def test_beide_schwellen_erfuellt_ergibt_pass() -> None:
    earnings = [_payment(f"h{i}") for i in range(MIN_PAYMENTS)]
    demand = [_challenge(f"h{i}", f"fp{i % MIN_DISTINCT_PAYERS}") for i in range(MIN_PAYMENTS)]
    result = _run(earnings, demand)
    assert result["verdict"] == "PASS"
    assert result["settled_payments_in_window"] == MIN_PAYMENTS
    assert result["distinct_payer_fps_in_window"] == MIN_DISTINCT_PAYERS


def test_genug_zahlungen_aber_zu_wenige_payer_ist_fail() -> None:
    """Fünf Zahlungen einer einzigen Quelle sind kein Nachfragesignal."""
    earnings = [_payment(f"h{i}") for i in range(MIN_PAYMENTS)]
    demand = [_challenge(f"h{i}", "fp-immer-derselbe") for i in range(MIN_PAYMENTS)]
    result = _run(earnings, demand)
    assert result["verdict"] == "FAIL"
    assert result["distinct_payer_fps_in_window"] == 1
    failed = [c["name"] for c in result["checks"] if not c["ok"]]
    assert failed == ["distinct_payer_fingerprints"]


def test_genug_payer_aber_zu_wenige_zahlungen_ist_fail() -> None:
    count = MIN_PAYMENTS - 1
    earnings = [_payment(f"h{i}") for i in range(count)]
    demand = [_challenge(f"h{i}", f"fp{i}") for i in range(count)]
    result = _run(earnings, demand)
    assert result["verdict"] == "FAIL"
    failed = [c["name"] for c in result["checks"] if not c["ok"]]
    assert failed == ["settled_payments"]


@pytest.mark.parametrize(
    ("row", "erwarteter_grund"),
    [
        (
            _payment("h-vor-fenster", settled="2026-07-02T06:01:47+00:00"),
            "settled ausserhalb des Fensters",
        ),
        (
            _payment("h-nach-fenster", settled="2026-08-04T00:00:01+00:00"),
            "settled ausserhalb des Fensters",
        ),
        (
            _payment("h-fremdes-memo", memo="kai-pay: KAI receive"),
            "memo trägt keinen kai-oracle:-Präfix",
        ),
    ],
)
def test_ausschlussgruende_werden_benannt(row: dict[str, Any], erwarteter_grund: str) -> None:
    result = _run([row], [_challenge(str(row["payment_hash"]), "fp-a")])
    assert result["settled_payments_in_window"] == 0
    considered = result["earnings_rows_considered"]
    assert len(considered) == 1
    assert erwarteter_grund in considered[0]["excluded_because"]


def test_fremder_scope_zaehlt_nicht() -> None:
    row = _payment("h-fremd", memo="kai-oracle:nicht-registriert")
    result = _run([row], [_challenge("h-fremd", "fp-a")])
    assert result["settled_payments_in_window"] == 0
    assert "scope" in " ".join(result["earnings_rows_considered"][0]["excluded_because"])


def test_zahlung_ohne_settle_zeitstempel_wird_ausgeschlossen_statt_geraten() -> None:
    row = {"payment_hash": "h-kaputt", "memo": "kai-oracle:fee-series", "amount_sat": 10}
    result = _run([row], [])
    assert result["settled_payments_in_window"] == 0
    assert (
        "kein auswertbarer Settle-Zeitstempel"
        in result["earnings_rows_considered"][0]["excluded_because"]
    )


def test_settled_at_als_unix_string_wird_verstanden() -> None:
    """Der echte Ledger schreibt ``"settled_at": "1783155159"`` — als String."""
    parsed = _parse_ts("1783155159")
    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed == datetime.fromtimestamp(1783155159, tz=UTC)


def test_settled_at_schlaegt_ts_als_zeitquelle() -> None:
    """``ts`` ist der Buchungszeitpunkt, maßgeblich ist der Settle-Zeitpunkt."""
    unix_in_window = str(int(datetime(2026, 7, 20, 12, 0, tzinfo=UTC).timestamp()))
    row = {
        "ts": "2026-09-01T00:00:00+00:00",  # Buchung weit ausserhalb
        "payment_hash": "h-late-booking",
        "amount_sat": 10,
        "memo": "kai-oracle:fee-series",
        "settled_at": unix_in_window,
    }
    result = _run([row], [_challenge("h-late-booking", "fp-a")])
    assert result["settled_payments_in_window"] == 1
    assert result["earnings_rows_considered"][0]["settle_time_source"] == "settled_at"


def test_payer_index_nimmt_nur_challenge_events_und_erste_nennung() -> None:
    rows = [
        _challenge("h1", "fp-erste"),
        {"event": "l402_access_granted", "payment_hash": "h1", "requester_fp": ""},
        _challenge("h1", "fp-spaeter"),
    ]
    assert _payer_index(rows) == {"h1": "fp-erste"}


def test_zahlung_ohne_zurechenbaren_payer_zaehlt_nicht_als_distinkter_payer() -> None:
    """Ohne Challenge-Join gibt es keinen Fingerprint — und keinen stillen Ersatz."""
    result = _run([_payment("h-ohne-challenge")], [])
    assert result["settled_payments_in_window"] == 1
    assert result["distinct_payer_fps_in_window"] == 0
    assert result["verdict"] == "FAIL"


def test_divergence_guard_rejects_rule_without_the_sealed_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Weicht der Regeltext von den Skript-Schwellen ab, bricht der Lauf ab.

    Der Divergenzschutz ist die einzige Sicherung dagegen, dass Skript und
    versiegelte Regel unbemerkt auseinanderlaufen — er war in der ersten Fassung
    implementiert, aber ungetestet (Luecke aus PR #630 uebernommen).
    """
    tampered = {
        "sealed_rule": {
            "payment_branch": {
                "criterion": ">=2 settled L402-Payments von >=1 distinkten Payer-Fingerprints",
                "scopes_counted": ["fee-series", "verdicts", "onchain-facts"],
                "source_of_truth": ["artifacts/ln_earnings_ledger.jsonl"],
                "window": {
                    "start_utc": "2026-07-04T09:22:07+00:00",
                    "end_utc": "2026-08-03T23:59:59+00:00",
                },
            }
        }
    }
    rule_path = tmp_path / "rule.json"
    rule_path.write_text(json.dumps(tampered), encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv", ["c1_payment_branch_eval.py", "--rule", str(rule_path), "--json"]
    )

    with pytest.raises(SystemExit) as excinfo:
        main()

    assert "divergiert" in str(excinfo.value)


def test_real_ledger_shape_reproduces_zero_zero() -> None:
    """Die real gemessene Lage als Fixture: 2 Oracle-Zahlungen VOR dem Fenster,
    1 Nicht-Oracle-Zahlung (die 25k-sat lnurlp-Zeile). Ergebnis 0/0 = FAIL.

    Haelt das Verdikt vom 2026-08-05 gegen eine Regression fest, ohne die
    Live-Ledger zu brauchen (Luecke aus PR #630 uebernommen).
    """
    earnings = [
        _payment("3baf314f", settled="2026-07-02T06:01:47+00:00", memo="kai-oracle:onchain-facts"),
        _payment("973358cf", settled="2026-07-02T06:14:28+00:00", memo="kai-oracle:onchain-facts"),
        _payment("d88cfb62", settled="2026-07-04T08:52:39+00:00", memo="kai-pay: KAI receive"),
    ]
    demand = [_challenge("3baf314f", "beed052613f160c5")]

    result = _run(earnings, demand)

    assert result["verdict"] == "FAIL"
    assert result["settled_payments_in_window"] == 0
    assert result["distinct_payer_fps_in_window"] == 0
    assert result["qualifying_payments"] == []
