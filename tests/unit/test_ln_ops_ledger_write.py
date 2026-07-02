"""Write side of the LN value-layer ops audit-ledger (Sprint 4).

Every node-touching value-layer action (executed/error) is appended tamper-evident
to artifacts/ln_ops_ledger.jsonl. Append-only, fail-soft (audit must never kill the
send path), round-trips through the existing read side.
"""

from __future__ import annotations

import json

from app.lightning.ops_ledger import append_ln_op, read_recent_ln_ops


def test_append_writes_record_with_fields(tmp_path) -> None:
    p = tmp_path / "ops.jsonl"
    append_ln_op(
        "pay_invoice", "executed", plan={"amount_sat": 1000}, response={"preimage": "ab"}, path=p
    )
    line = json.loads(p.read_text(encoding="utf-8").strip())
    assert line["action"] == "pay_invoice"
    assert line["state"] == "executed"
    assert line["plan"]["amount_sat"] == 1000
    assert line["response"]["preimage"] == "ab"
    assert "ts" in line


def test_append_is_append_only_and_reads_back(tmp_path) -> None:
    p = tmp_path / "ops.jsonl"
    append_ln_op("send_coins", "executed", plan={"sat": 1}, path=p)
    append_ln_op("close_channel", "error", plan={}, path=p)
    ops = read_recent_ln_ops(path=p)
    assert [o["action"] for o in ops] == ["send_coins", "close_channel"]
    assert ops[1]["state"] == "error"


def test_append_failsoft_swallows_errors(tmp_path) -> None:
    # A directory where a file is expected → OSError on open; must NOT raise.
    bad = tmp_path / "as_dir.jsonl"
    bad.mkdir()
    append_ln_op("pay_invoice", "executed", plan={}, path=bad)  # no exception = pass
