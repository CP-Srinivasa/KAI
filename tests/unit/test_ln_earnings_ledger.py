"""Sprint 3b — incoming-earnings ledger (the souvereign treasury source for UC-7).

Every settled inbound payment is booked ONCE (idempotent via payment_hash) into an
append-only ledger; the Self-Funding treasury (Sprint 7) aggregates from here. No
capital path — pure accounting of money that already arrived.
"""

from __future__ import annotations

import json

from app.lightning.earnings_ledger import (
    append_ln_earning,
    read_recent_ln_earnings,
    record_settled_invoices,
)


def test_append_is_idempotent_per_payment_hash(tmp_path) -> None:
    p = tmp_path / "earnings.jsonl"
    assert append_ln_earning(payment_hash="ab", amount_sat=1000, source="oracle", path=p) is True
    # same payment_hash again → not double-booked
    assert append_ln_earning(payment_hash="ab", amount_sat=1000, source="oracle", path=p) is False
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["payment_hash"] == "ab" and rec["amount_sat"] == 1000 and rec["source"] == "oracle"
    assert "ts" in rec


def test_append_distinct_hashes_both_booked(tmp_path) -> None:
    p = tmp_path / "e.jsonl"
    append_ln_earning(payment_hash="aa", amount_sat=1, source="x", path=p)
    append_ln_earning(payment_hash="bb", amount_sat=2, source="x", path=p)
    recs = read_recent_ln_earnings(path=p)
    assert [r["payment_hash"] for r in recs] == ["aa", "bb"]


def test_record_settled_invoices_filters_and_dedups(tmp_path) -> None:
    p = tmp_path / "e.jsonl"
    import base64

    def _rhash(hexs: str) -> str:
        return base64.b64encode(bytes.fromhex(hexs)).decode("ascii")

    invoices = [
        {"r_hash": _rhash("11" * 32), "amt_paid_sat": "500", "settled": True, "memo": "uc4"},
        {"r_hash": _rhash("22" * 32), "amt_paid_sat": "0", "settled": False},  # unsettled → skip
        {"r_hash": _rhash("33" * 32), "amt_paid_sat": "700", "settled": True},
    ]
    n = record_settled_invoices(invoices, path=p, source="l402")
    assert n == 2  # only the two settled
    # idempotent: re-running the same set books nothing new
    assert record_settled_invoices(invoices, path=p, source="l402") == 0
    recs = read_recent_ln_earnings(path=p)
    assert sum(r["amount_sat"] for r in recs) == 1200


def test_read_recent_missing_file_empty(tmp_path) -> None:
    assert read_recent_ln_earnings(path=tmp_path / "nope.jsonl") == []
