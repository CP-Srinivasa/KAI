"""Write side of the LN value-layer ops audit-ledger (Sprint 4).

Every node-touching value-layer action (executed/error) is appended tamper-evident
to artifacts/ln_ops_ledger.jsonl. Append-only, fail-soft (audit must never kill the
send path), round-trips through the existing read side.
"""

from __future__ import annotations

import json

from app.lightning.ops_ledger import (
    append_ln_op,
    attest_ln_ops_tip,
    migrate_legacy_ln_ops,
    payment_shadow_evidence,
    prepare_ln_intent,
    read_recent_ln_ops,
    verify_ln_ops_ledger,
)
from app.truth.ledger import verify_ledger


def test_append_writes_record_with_fields(tmp_path) -> None:
    p = tmp_path / "ops.jsonl"
    prepare_ln_intent("pay_invoice", plan={"amount_sat": 1000}, intent_id="p1", path=p)
    append_ln_op(
        "pay_invoice",
        "executed",
        plan={"amount_sat": 1000},
        intent_id="p1",
        response={"preimage": "ab"},
        path=p,
    )
    line = json.loads(p.read_text(encoding="utf-8").splitlines()[-1])
    assert line["action"] == "pay_invoice"
    assert line["state"] == "executed"
    assert line["schema"] == "ln-ops-public/v2"
    assert line["plan"]["amount_sat"] == 1000
    assert "preimage" not in line["response"]
    assert len(line["response"]["preimage_hash"]) == 64
    assert "ts" in line


def test_append_is_append_only_and_reads_back(tmp_path) -> None:
    p = tmp_path / "ops.jsonl"
    prepare_ln_intent("send_coins", plan={"amount_sat": 1}, intent_id="s1", path=p)
    append_ln_op("send_coins", "executed", plan={"amount_sat": 1}, intent_id="s1", path=p)
    prepare_ln_intent("close_channel", plan={}, intent_id="c1", path=p)
    append_ln_op("close_channel", "error", plan={}, intent_id="c1", path=p)
    ops = read_recent_ln_ops(path=p)
    outcomes = [row for row in ops if row["state"] != "intent"]
    assert [o["action"] for o in outcomes] == ["send_coins", "close_channel"]
    assert outcomes[1]["state"] == "error"


def test_append_failsoft_swallows_errors(tmp_path) -> None:
    # A directory where a file is expected → OSError on open; must NOT raise.
    bad = tmp_path / "as_dir.jsonl"
    bad.mkdir()
    append_ln_op(
        "pay_invoice", "executed", plan={}, intent_id="missing", path=bad
    )  # no exception = pass


def test_writer_and_reader_redact_bolt11_recipient_and_route_hops(tmp_path) -> None:
    p = tmp_path / "ops.jsonl"
    invoice = "lnbc250u1privateinvoice"
    plan = {
        "payment_request": invoice,
        "fee_limit_sat": 10,
        "expires_at_unix": 2_000_000_000,
    }
    prepare_ln_intent(
        "pay_invoice",
        plan=plan,
        intent_id="p2",
        path=p,
    )
    append_ln_op(
        "pay_invoice",
        "executed",
        plan=plan,
        intent_id="p2",
        response={
            "payment_preimage": "11" * 32,
            "payment_route": {
                "total_amt": "25001",
                "total_fees": "1",
                "hops": [{"pub_key": "secret-peer", "chan_id": "42"}],
            },
        },
        path=p,
    )
    text = p.read_text(encoding="utf-8")
    assert invoice not in text
    assert "secret-peer" not in text
    assert '"hops"' not in text
    row = read_recent_ln_ops(path=p)[-1]
    assert row["plan"]["amount_sat"] == 25_000
    assert row["plan"]["expires_at_unix"] == 2_000_000_000
    assert row["response"]["route_summary"]["total_amt_sat"] == 25_001
    assert row["response"]["amount_sat"] == 25_001


def test_reader_redacts_legacy_rows_before_public_output(tmp_path) -> None:
    p = tmp_path / "legacy.jsonl"
    p.write_text(
        json.dumps(
            {
                "ts": "2026-08-01T00:00:00+00:00",
                "action": "keysend",
                "state": "executed",
                "plan": {"dest_pubkey_hex": "02-secret", "amt_sat": 7},
                "response": {"preimage": "22" * 32},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    public = read_recent_ln_ops(path=p)[0]
    rendered = json.dumps(public)
    assert "02-secret" not in rendered
    assert "22" * 32 not in rendered
    assert public["plan"]["amount_sat"] == 7


def test_write_ahead_intent_is_fsynced_and_chain_verifies(tmp_path) -> None:
    p = tmp_path / "ops.jsonl"
    intent = prepare_ln_intent(
        "pay_invoice", plan={"payment_request": "lnbc10u1private"}, intent_id="intent-1", path=p
    )
    assert intent["state"] == "intent"
    assert len(intent["record_hash"]) == 64
    report = verify_ln_ops_ledger(p)
    assert report["ok"] is True and report["open_intents"] == ["intent-1"]

    append_ln_op(
        "pay_invoice",
        "executed",
        plan={"payment_request": "lnbc10u1private"},
        intent_id="intent-1",
        response={"payment_preimage": "33" * 32},
        path=p,
    )
    report = verify_ln_ops_ledger(p)
    assert report["ok"] is True and report["open_intents"] == []


def test_payment_hash_dedup_blocks_new_intent_id(tmp_path) -> None:
    from pytest import raises

    from app.lightning.ops_ledger import LightningOpsLedgerError

    ledger = tmp_path / "ops.jsonl"
    plan = {"amount_sat": 1000, "payment_hash": "11" * 32}
    prepare_ln_intent("pay_invoice", plan=plan, intent_id="first", path=ledger)
    with raises(LightningOpsLedgerError, match="payment_hash already journalled"):
        prepare_ln_intent("pay_invoice", plan=plan, intent_id="second", path=ledger)


def test_intent_persists_only_allowlisted_policy_authorization(tmp_path) -> None:
    ledger = tmp_path / "ops.jsonl"
    prepare_ln_intent(
        "send_coins",
        plan={"amount_sat": 1000},
        intent_id="auth-1",
        authorization={
            "policy_decision": "needs_confirm",
            "confirmation": "hotp",
            "plan_hash": "ab" * 32,
            "hotp_code": "must-never-persist",
        },
        path=ledger,
    )
    record = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
    assert record["authorization"] == {
        "policy_decision": "needs_confirm",
        "confirmation": "hotp",
        "plan_hash": "ab" * 32,
    }
    assert "must-never-persist" not in ledger.read_text(encoding="utf-8")


def test_tamper_is_detected(tmp_path) -> None:
    p = tmp_path / "ops.jsonl"
    prepare_ln_intent("keysend", plan={"amt_sat": 5}, intent_id="k1", path=p)
    rows = p.read_text(encoding="utf-8").splitlines()
    row = json.loads(rows[0])
    row["plan"]["amount_sat"] = 999
    p.write_text(json.dumps(row) + "\n", encoding="utf-8")
    assert verify_ln_ops_ledger(p)["ok"] is False


def test_legacy_migration_is_non_destructive_redacted_and_verified(tmp_path) -> None:
    source = tmp_path / "legacy.jsonl"
    destination = tmp_path / "v2.jsonl"
    invoice = "lnbc250u1migration-secret"
    source.write_text(
        json.dumps(
            {
                "ts": "2026-07-02T05:46:20+00:00",
                "action": "pay_invoice",
                "state": "error",
                "plan": {"payment_request": invoice},
                "response": {"payment_preimage": "44" * 32},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    original = source.read_bytes()
    report = migrate_legacy_ln_ops(source, destination)
    assert source.read_bytes() == original
    assert report["verification"]["ok"] is True
    assert report["written_records"] == 2
    migrated = destination.read_text(encoding="utf-8")
    assert invoice not in migrated
    assert "44" * 32 not in migrated


def test_ln_tip_attests_into_truth_chain_idempotently(tmp_path) -> None:
    ops = tmp_path / "ops.jsonl"
    truth = tmp_path / "truth.jsonl"
    prepare_ln_intent("create_invoice", plan={"value_sat": 10}, intent_id="i1", path=ops)
    append_ln_op(
        "create_invoice", "executed", plan={"value_sat": 10}, intent_id="i1", path=ops
    )
    first = attest_ln_ops_tip(ops_path=ops, truth_path=truth, mirror_audit=False)
    second = attest_ln_ops_tip(ops_path=ops, truth_path=truth, mirror_audit=False)
    assert first == {"total": 1, "attested": 1, "skipped": 0}
    assert second == {"total": 1, "attested": 0, "skipped": 1}
    assert verify_ledger(truth)["ok"] is True


def test_payment_shadow_gate_requires_twenty_matching_samples(tmp_path) -> None:
    ops = tmp_path / "ops.jsonl"
    for index in range(20):
        intent_id = f"p{index}"
        plan = {"amount_sat": 10, "payment_hash": f"{index:064x}"}
        prepare_ln_intent("pay_invoice", plan=plan, intent_id=intent_id, path=ops)
        append_ln_op(
            "pay_invoice",
            "executed",
            plan=plan,
            response={"sync_status": "SUCCEEDED", "track_v2_status": "SUCCEEDED"},
            intent_id=intent_id,
            path=ops,
        )
    report = payment_shadow_evidence(ops)
    assert report["total_comparisons"] == 20
    assert report["eligible_for_v2_cutover"] is True
