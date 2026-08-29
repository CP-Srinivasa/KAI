"""G2 — the resolver, measured against the thirteen actions that really happened.

The fixture below is the actual money journal of this node (redacted v2 shape) and
the actual node evidence, both taken from the KMA-20260827 audit. That is the
point: an evaluator validated only against invented data proves that it runs, not
that it is right. The pre-registration's gate is exactly the table in
``G2_FORENSIK.md`` §1 — so the test asserts against that table, action by action.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.lightning.ops_resolution import (
    CONTRADICTED,
    EXECUTED_CONFIRMED,
    NOT_EXECUTED_CONFIRMED,
    NOT_REAL,
    UNRESOLVED,
    resolve_journal,
    resolve_record,
)


def _unix(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp())


def _pair(
    seq: int, ts: str, action: str, terminal: str, plan: dict[str, Any]
) -> list[dict[str, Any]]:
    intent_id = f"i{seq}"
    return [
        {
            "seq": seq,
            "ts": ts,
            "action": action,
            "state": "intent",
            "intent_id": intent_id,
            "plan": plan,
        },
        {
            "seq": seq + 1,
            "ts": ts,
            "action": action,
            "state": terminal,
            "intent_id": intent_id,
            "plan": plan,
        },
    ]


def _journal() -> list[dict[str, Any]]:
    """The 13 actions, redacted exactly as v2 stores them (peer_hash, no pubkey)."""
    rows: list[dict[str, Any]] = []
    for seq, ts, peer in (
        (1, "2026-07-01T21:04:02+00:00", "fce15c4c"),
        (3, "2026-07-01T21:05:15+00:00", "fce15c4c"),
        (5, "2026-07-01T21:47:14+00:00", "fce15c4c"),
        (7, "2026-07-01T22:00:04+00:00", "3a403058"),
        (9, "2026-07-01T22:02:59+00:00", "c345d282"),
        (11, "2026-07-01T22:03:28+00:00", "daceb3d6"),
    ):
        rows += _pair(
            seq, ts, "open_channel", "error", {"local_funding_sat": 100_000, "peer_hash": peer}
        )
    rows += _pair(
        13,
        "2026-07-01T22:07:23+00:00",
        "open_channel",
        "error",
        {"local_funding_sat": 400_000, "peer_hash": "fce15c4c"},
    )
    rows += _pair(15, "2026-07-02T05:46:20+00:00", "pay_invoice", "error", {"amount_sat": 25_000})
    rows += _pair(17, "2026-07-03T14:36:13+00:00", "pay_invoice", "executed", {"amount_sat": 2_100})
    rows += _pair(
        19,
        "2026-08-05T12:38:04+00:00",
        "send_coins",
        "error",
        {"amount_sat": 1_000, "recipient_hash": "5fe6d83b"},
    )
    rows += _pair(
        21,
        "2026-08-05T12:38:12+00:00",
        "open_channel",
        "executed",
        {"local_funding_sat": 50_000, "peer_hash": "d4ddd2ca"},
    )
    rows += _pair(
        23,
        "2026-08-05T12:39:08+00:00",
        "send_coins",
        "error",
        {"amount_sat": 1_000, "recipient_hash": "5fe6d83b"},
    )
    rows += _pair(
        25,
        "2026-08-05T12:39:15+00:00",
        "open_channel",
        "executed",
        {"local_funding_sat": 50_000, "peer_hash": "d4ddd2ca"},
    )
    return rows


def _node() -> dict[str, Any]:
    """Node truth as measured on 2026-08-27: 8 payments, one wallet debit."""
    return {
        "payments": [
            {
                "creation_date": _unix("2026-07-02T05:46:10+00:00"),
                "value_sat": 25_000,
                "status": "SUCCEEDED",
                "payment_hash": "b764db71",
            },
            {
                "creation_date": _unix("2026-07-03T14:36:12+00:00"),
                "value_sat": 2_100,
                "status": "SUCCEEDED",
                "payment_hash": "d6d4d08c",
            },
            {
                "creation_date": _unix("2026-07-03T15:01:28+00:00"),
                "value_sat": 25_000,
                "status": "SUCCEEDED",
                "payment_hash": "e322621a",
            },
            {
                "creation_date": _unix("2026-07-04T16:24:53+00:00"),
                "value_sat": 30,
                "status": "SUCCEEDED",
                "payment_hash": "8906ce78",
            },
            {
                "creation_date": _unix("2026-07-04T16:30:40+00:00"),
                "value_sat": 1_020,
                "status": "FAILED",
                "payment_hash": "8330d60c",
            },
            {
                "creation_date": _unix("2026-07-04T16:30:45+00:00"),
                "value_sat": 72,
                "status": "FAILED",
                "payment_hash": "8b843a41",
            },
            {
                "creation_date": _unix("2026-07-04T16:43:26+00:00"),
                "value_sat": 102,
                "status": "SUCCEEDED",
                "payment_hash": "febdd805",
            },
            {
                "creation_date": _unix("2026-07-04T16:52:46+00:00"),
                "value_sat": 1_020,
                "status": "SUCCEEDED",
                "payment_hash": "e20b2b7f",
            },
        ],
        "wallet_debits": [
            {
                "unix": _unix("2026-07-01T22:11:16+00:00"),
                "amount_sat": 400_308,
                "evidence": "A12-072",
            },
        ],
    }


#: The unredacted v1 plans of the four rows written from the manual session.
_LEGACY_FIXTURES = {
    19: {"addr": "bc1q", "amount_sat": 1_000, "sat_per_vbyte": 0},
    21: {"node_pubkey_hex": "02ab", "local_funding_sat": 50_000, "sat_per_vbyte": 0},
    23: {"addr": "bc1q", "amount_sat": 1_000, "sat_per_vbyte": 0},
    25: {"node_pubkey_hex": "02ab", "local_funding_sat": 50_000, "sat_per_vbyte": 0},
}

#: G2_FORENSIK.md §1, as a machine-checkable expectation.
_EXPECTED = {
    1: NOT_EXECUTED_CONFIRMED,
    3: NOT_EXECUTED_CONFIRMED,
    5: NOT_EXECUTED_CONFIRMED,
    7: NOT_EXECUTED_CONFIRMED,
    9: NOT_EXECUTED_CONFIRMED,
    11: NOT_EXECUTED_CONFIRMED,
    13: EXECUTED_CONFIRMED,  # the 400.000 sat open booked as error
    15: EXECUTED_CONFIRMED,  # the 25.000 sat payment booked as error
    17: EXECUTED_CONFIRMED,  # the only correctly booked action
    19: NOT_REAL,
    21: NOT_REAL,
    23: NOT_REAL,
    25: NOT_REAL,
}


def test_the_gate_all_thirteen_actions_resolve_as_the_forensics_found_them() -> None:
    report = resolve_journal(_journal(), _node(), legacy_plans=_LEGACY_FIXTURES)
    assert report["n_actions"] == 13
    assert report["unresolved"] == 0
    got = {item["seq"]: item["verdict"] for item in report["resolutions"]}
    assert got == _EXPECTED


def test_the_two_unproven_rows_are_the_ones_that_moved_money() -> None:
    """The heart of the pre-registration: 425.000 sat sat in `error` for two months."""
    report = resolve_journal(_journal(), _node(), legacy_plans=_LEGACY_FIXTURES)
    surprises = {
        item["seq"]
        for item in report["disagreements"]
        if item["verdict"] == EXECUTED_CONFIRMED and item["journal_state"] == "error"
    }
    assert surprises == {13, 15}


def test_without_the_legacy_plans_a_fixture_that_claims_success_is_still_caught() -> None:
    """v2 alone cannot see `02ab` — but it can see a claim the node does not support."""
    report = resolve_journal(_journal(), _node())
    got = {item["seq"]: item["verdict"] for item in report["resolutions"]}
    assert got[21] == CONTRADICTED
    assert got[25] == CONTRADICTED
    # And the honest limit, stated rather than hidden: a fixture booked as `error`
    # is indistinguishable from a real failure using node evidence alone.
    assert got[19] == NOT_EXECUTED_CONFIRMED
    assert got[23] == NOT_EXECUTED_CONFIRMED


def test_negative_control_changing_the_node_changes_the_verdict() -> None:
    """If the wallet movement disappears, the 400k open must stop being confirmed."""
    node = _node()
    node["wallet_debits"] = []
    report = resolve_journal(_journal(), node, legacy_plans=_LEGACY_FIXTURES)
    got = {item["seq"]: item["verdict"] for item in report["resolutions"]}
    assert got[13] == NOT_EXECUTED_CONFIRMED, "the resolver must read the node, not guess"
    assert got[15] == EXECUTED_CONFIRMED, "the payment evidence is untouched"


def test_negative_control_removing_the_payment_changes_the_verdict() -> None:
    node = _node()
    node["payments"] = [p for p in node["payments"] if p["payment_hash"] != "b764db71"]
    got = {
        item["seq"]: item["verdict"]
        for item in resolve_journal(_journal(), node, legacy_plans=_LEGACY_FIXTURES)["resolutions"]
    }
    assert got[15] == NOT_EXECUTED_CONFIRMED


def test_missing_evidence_is_unresolved_never_a_guess() -> None:
    report = resolve_journal(_journal(), {}, legacy_plans=_LEGACY_FIXTURES)
    verdicts = {
        item["verdict"] for item in report["resolutions"] if item["seq"] not in _LEGACY_FIXTURES
    }
    assert verdicts == {UNRESOLVED}
    assert report["unresolved"] == 9


def test_a_closed_channel_is_still_a_real_open() -> None:
    """Resolving from the channel list would call a since-closed open 'never happened'."""
    record = {
        "seq": 13,
        "ts": "2026-07-01T22:07:23+00:00",
        "action": "open_channel",
        "state": "error",
        "plan": {"local_funding_sat": 400_000},
    }
    node = {"channels": [], "wallet_debits": _node()["wallet_debits"]}
    assert resolve_record(record, node)["verdict"] == EXECUTED_CONFIRMED


def test_a_debit_before_the_row_cannot_be_caused_by_it() -> None:
    record = {
        "seq": 13,
        "ts": "2026-07-01T22:07:23+00:00",
        "action": "open_channel",
        "state": "error",
        "plan": {"local_funding_sat": 400_000},
    }
    node = {
        "wallet_debits": [
            {"unix": _unix("2026-07-01T20:00:00+00:00"), "amount_sat": 400_308, "evidence": "x"}
        ]
    }
    assert resolve_record(record, node)["verdict"] == NOT_EXECUTED_CONFIRMED


def test_the_matching_window_does_not_merge_the_two_25k_payments() -> None:
    """Both are 25.000 sat and both SUCCEEDED; only one belongs to the journal row."""
    report = resolve_journal(_journal(), _node(), legacy_plans=_LEGACY_FIXTURES)
    row = next(item for item in report["resolutions"] if item["seq"] == 15)
    assert row["node_evidence"] == ["b764db71"], "must match 05:46:10, not the 15:01:28 swap"


def test_the_report_carries_its_decomposition_not_just_a_total() -> None:
    report = resolve_journal(_journal(), _node(), legacy_plans=_LEGACY_FIXTURES)
    assert report["by_verdict"] == {
        NOT_EXECUTED_CONFIRMED: 6,
        EXECUTED_CONFIRMED: 3,
        NOT_REAL: 4,
    }
    assert sum(report["by_verdict"].values()) == report["n_actions"]
