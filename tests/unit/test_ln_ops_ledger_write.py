"""Write side of the LN value-layer ops audit-ledger — v1 (live) and v2 (parallel).

Part 1 pins the LIVE v1 behaviour byte for byte: value_layer/ln_control/dashboard
call ``append_ln_op``/``read_recent_ln_ops`` today and PR-B must not move a comma
for them (append-only, fail-soft, legacy shape, no redaction).

Part 2 exercises the v2 machinery that nothing calls yet: write-ahead intent
(fail-closed), redaction at the writer boundary, hash chaining, lifecycle
verification, the M-4 retry window, tail/interior repair refusal (M-5) and the
non-destructive migration with BL-3 provenance.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.lightning.ops_ledger import (
    LightningOpsLedgerError,
    append_ln_op,
    append_ln_outcome,
    attest_ln_ops_tip,
    ln_ops_v2_path,
    migrate_legacy_ln_ops,
    normalize_payment_hash,
    prepare_ln_intent,
    read_recent_ln_ops,
    verify_ln_ops_ledger,
)
from app.truth.ledger import verify_ledger

_BASE = datetime(2026, 8, 5, 12, 0, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Part 1 — v1 stays EXACTLY as it is today (bestandsschutz for live callers).
# --------------------------------------------------------------------------- #


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


def test_v1_signature_takes_no_intent_id(tmp_path) -> None:
    # Coexistence guard: PR-B must not sneak the v2 write-ahead contract into the
    # live v1 writer. value_layer.execute() calls append_ln_op WITHOUT an intent.
    with pytest.raises(TypeError):
        append_ln_op(  # type: ignore[call-arg]
            "pay_invoice", "executed", plan={}, intent_id="x", path=tmp_path / "ops.jsonl"
        )


def test_v1_and_v2_journals_do_not_touch_each_other(tmp_path) -> None:
    v1 = tmp_path / "ops.jsonl"
    v2 = tmp_path / "ops_v2.jsonl"
    append_ln_op("keysend", "executed", plan={"amt_sat": 7}, path=v1)
    prepare_ln_intent("keysend", plan={"amount_sat": 7}, intent_id="k1", path=v2)

    # v1 file: still one flat, unchained, unredacted legacy row.
    v1_rows = [json.loads(line) for line in v1.read_text(encoding="utf-8").splitlines()]
    assert len(v1_rows) == 1
    assert "record_hash" not in v1_rows[0] and "intent_id" not in v1_rows[0]
    assert v1_rows[0]["plan"] == {"amt_sat": 7}  # no redaction on the v1 path

    # v2 file: one chained intent, and the v1 reader never sees it.
    assert verify_ln_ops_ledger(v2)["open_intents"] == ["k1"]
    assert [row["action"] for row in read_recent_ln_ops(path=v1)] == ["keysend"]


# --------------------------------------------------------------------------- #
# Part 2 — v2 machinery (no production caller yet; wired in PR-C).
# --------------------------------------------------------------------------- #


def test_v2_default_path_is_separate_and_env_overridable(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("APP_LN_OPS_LEDGER_V2_PATH", raising=False)
    assert ln_ops_v2_path() == Path("artifacts/ln_ops_ledger_v2.jsonl")
    monkeypatch.setenv("APP_LN_OPS_LEDGER_V2_PATH", str(tmp_path / "custom.jsonl"))
    assert ln_ops_v2_path() == tmp_path / "custom.jsonl"


def test_write_ahead_intent_is_chained_and_lifecycle_verifies(tmp_path) -> None:
    p = tmp_path / "ops.jsonl"
    intent = prepare_ln_intent(
        "pay_invoice", plan={"payment_request": "lnbc10u1private"}, intent_id="intent-1", path=p
    )
    assert intent["state"] == "intent"
    assert intent["seq"] == 1 and intent["prev_hash"] == "0" * 64
    assert len(intent["record_hash"]) == 64
    report = verify_ln_ops_ledger(p)
    assert report["ok"] is True and report["open_intents"] == ["intent-1"]

    assert (
        append_ln_outcome(
            "pay_invoice",
            "executed",
            plan={"payment_request": "lnbc10u1private"},
            intent_id="intent-1",
            response={"payment_preimage": "33" * 32},
            path=p,
        )
        is True
    )
    report = verify_ln_ops_ledger(p)
    assert report["ok"] is True and report["open_intents"] == []


def test_outcome_without_intent_is_refused_but_failsoft(tmp_path) -> None:
    p = tmp_path / "ops.jsonl"
    # No prepared intent → the outcome must not be journalled, and must not raise
    # (LND may already have moved value; raising cannot undo it).
    assert append_ln_outcome("pay_invoice", "executed", plan={}, intent_id="ghost", path=p) is False
    assert not p.exists() or p.read_text(encoding="utf-8").strip() == ""


def test_writer_redacts_bolt11_recipient_preimage_and_route_hops(tmp_path) -> None:
    p = tmp_path / "ops.jsonl"
    invoice = "lnbc250u1privateinvoice"
    plan = {"payment_request": invoice, "fee_limit_sat": 10, "expires_at_unix": 2_000_000_000}
    prepare_ln_intent("pay_invoice", plan=plan, intent_id="p2", path=p)
    append_ln_outcome(
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
    assert "11" * 32 not in text
    row = json.loads(text.splitlines()[-1])
    assert row["plan"]["amount_sat"] == 25_000
    assert row["plan"]["expires_at_unix"] == 2_000_000_000
    assert row["response"]["route_summary"] == {
        "total_amt_sat": 25_001,
        "total_fees_sat": 1,
        "total_time_lock": 0,
    }
    assert row["response"]["amount_sat"] == 25_001
    assert len(row["response"]["preimage_hash"]) == 64


def test_intent_persists_only_allowlisted_authorization(tmp_path) -> None:
    p = tmp_path / "ops.jsonl"
    prepare_ln_intent(
        "send_coins",
        plan={"amount_sat": 1000, "addr": "bc1-secret"},
        intent_id="auth-1",
        authorization={
            "policy_decision": "needs_confirm",
            "confirmation": "hotp",
            "plan_hash": "ab" * 32,
            "hotp_code": "must-never-persist",
        },
        path=p,
    )
    text = p.read_text(encoding="utf-8")
    record = json.loads(text.splitlines()[0])
    assert record["authorization"] == {
        "policy_decision": "needs_confirm",
        "confirmation": "hotp",
        "plan_hash": "ab" * 32,
    }
    assert "must-never-persist" not in text
    assert "bc1-secret" not in text


def test_tamper_is_detected(tmp_path) -> None:
    p = tmp_path / "ops.jsonl"
    prepare_ln_intent("keysend", plan={"amt_sat": 5}, intent_id="k1", path=p)
    row = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
    row["plan"]["amount_sat"] = 999
    p.write_text(json.dumps(row) + "\n", encoding="utf-8")
    report = verify_ln_ops_ledger(p)
    assert report["ok"] is False
    assert any("record_hash mismatch" in err["reason"] for err in report["errors"])


def test_legacy_unchained_file_refuses_new_money_events(tmp_path) -> None:
    p = tmp_path / "ops.jsonl"
    p.write_text(
        json.dumps({"ts": "2026-08-01T00:00:00+00:00", "action": "keysend", "state": "executed"})
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(LightningOpsLedgerError, match="migrate before new money events"):
        prepare_ln_intent("keysend", plan={"amount_sat": 1}, intent_id="k9", path=p)


def test_payment_hash_is_normalised_to_hex(tmp_path) -> None:
    # MI-1: LND REST hands back base64 ``r_hash`` while the operator speaks hex —
    # both spellings must land in the journal as the SAME hex string, otherwise the
    # M-4 duplicate guard is blind.
    import base64

    raw = bytes(range(32))
    b64 = base64.b64encode(raw).decode()
    assert normalize_payment_hash(b64) == raw.hex()
    assert normalize_payment_hash(base64.urlsafe_b64encode(raw).decode()) == raw.hex()
    assert normalize_payment_hash(raw.hex().upper()) == raw.hex()

    p = tmp_path / "ops.jsonl"
    prepare_ln_intent(
        "pay_invoice", plan={"amount_sat": 1, "payment_hash": b64}, intent_id="h1", path=p
    )
    assert json.loads(p.read_text(encoding="utf-8").splitlines()[0])["plan"]["payment_hash"] == (
        raw.hex()
    )


# ---- M-5: a torn/corrupt journal refuses to be extended, and says how to fix it ----


def test_torn_tail_refuses_append_and_points_at_the_runbook(tmp_path) -> None:
    p = tmp_path / "ops.jsonl"
    prepare_ln_intent("create_invoice", plan={"value_sat": 5}, intent_id="i1", path=p)
    with p.open("a", encoding="utf-8") as handle:
        handle.write('{"ts": "2026-08-05T00:00:00+00:0')  # power cut mid-write
    with pytest.raises(LightningOpsLedgerError) as excinfo:
        prepare_ln_intent("create_invoice", plan={"value_sat": 6}, intent_id="i2", path=p)
    message = str(excinfo.value)
    assert "tail unreadable" in message
    assert "docs/runbooks/ln_ops_ledger_v2_migration.md" in message
    assert "Tail-Recovery" in message


def test_corrupt_interior_row_refuses_append_distinctly(tmp_path) -> None:
    # An interior corruption is NOT a torn write — appending onto the last parseable
    # row would silently fork the money journal, so it must refuse with its own label.
    p = tmp_path / "ops.jsonl"
    prepare_ln_intent("create_invoice", plan={"value_sat": 5}, intent_id="i1", path=p)
    prepare_ln_intent("create_invoice", plan={"value_sat": 6}, intent_id="i2", path=p)
    rows = p.read_text(encoding="utf-8").splitlines()
    rows[0] = rows[0][:40]
    p.write_text("".join(f"{row}\n" for row in rows), encoding="utf-8")
    with pytest.raises(LightningOpsLedgerError, match="interior line 1 unreadable"):
        prepare_ln_intent("create_invoice", plan={"value_sat": 7}, intent_id="i3", path=p)


# ---- M-4: dedup blocks a settled or LIVE invoice, never a dead one forever ----


def test_settled_payment_hash_is_blocked_forever(tmp_path) -> None:
    p = tmp_path / "ops.jsonl"
    plan = {"amount_sat": 1000, "payment_hash": "11" * 32}
    prepare_ln_intent("pay_invoice", plan=plan, intent_id="first", path=p, now=_BASE)
    append_ln_outcome("pay_invoice", "executed", plan=plan, intent_id="first", path=p)
    with pytest.raises(LightningOpsLedgerError, match="already settled"):
        prepare_ln_intent(
            "pay_invoice",
            plan=plan,
            intent_id="second",
            path=p,
            now=_BASE + timedelta(days=365),
        )


def test_live_intent_blocks_but_terminal_error_allows_retry(tmp_path) -> None:
    # M-4 reproduced: an honest failure used to brick the invoice permanently.
    p = tmp_path / "ops.jsonl"
    plan = {"amount_sat": 1000, "payment_hash": "22" * 32}
    prepare_ln_intent("pay_invoice", plan=plan, intent_id="first", path=p, now=_BASE)
    with pytest.raises(LightningOpsLedgerError, match="live intent"):
        prepare_ln_intent(
            "pay_invoice", plan=plan, intent_id="second", path=p, now=_BASE + timedelta(minutes=1)
        )
    append_ln_outcome("pay_invoice", "error", plan=plan, intent_id="first", response={}, path=p)
    retry = prepare_ln_intent(
        "pay_invoice", plan=plan, intent_id="second", path=p, now=_BASE + timedelta(minutes=2)
    )
    assert retry["intent_id"] == "second"
    assert verify_ln_ops_ledger(p)["ok"] is True


def test_crashed_intent_unblocks_after_invoice_expiry(tmp_path) -> None:
    # Crash between intent-write and send: no outcome will ever be written. The
    # invoice's own expiry is the honest release point — before it a retry could
    # double-pay, after it the invoice is unpayable.
    p = tmp_path / "ops.jsonl"
    expiry = int((_BASE + timedelta(minutes=10)).timestamp())
    plan = {"amount_sat": 1000, "payment_hash": "33" * 32, "expires_at_unix": expiry}
    prepare_ln_intent("pay_invoice", plan=plan, intent_id="crashed", path=p, now=_BASE)
    with pytest.raises(LightningOpsLedgerError, match="live intent"):
        prepare_ln_intent(
            "pay_invoice", plan=plan, intent_id="retry", path=p, now=_BASE + timedelta(minutes=9)
        )
    retry = prepare_ln_intent(
        "pay_invoice", plan=plan, intent_id="retry", path=p, now=_BASE + timedelta(minutes=11)
    )
    assert retry["intent_id"] == "retry"


def test_crashed_intent_without_expiry_unblocks_after_default_ttl(tmp_path) -> None:
    p = tmp_path / "ops.jsonl"
    plan = {"amount_sat": 1000, "payment_hash": "44" * 32}
    prepare_ln_intent("pay_invoice", plan=plan, intent_id="crashed", path=p, now=_BASE)
    with pytest.raises(LightningOpsLedgerError, match="live intent"):
        prepare_ln_intent(
            "pay_invoice", plan=plan, intent_id="retry", path=p, now=_BASE + timedelta(minutes=59)
        )
    assert (
        prepare_ln_intent(
            "pay_invoice", plan=plan, intent_id="retry", path=p, now=_BASE + timedelta(hours=1)
        )["intent_id"]
        == "retry"
    )


def test_intent_id_replay_is_always_refused(tmp_path) -> None:
    p = tmp_path / "ops.jsonl"
    prepare_ln_intent("create_invoice", plan={"value_sat": 1}, intent_id="dup", path=p)
    with pytest.raises(LightningOpsLedgerError, match="intent replay"):
        prepare_ln_intent("create_invoice", plan={"value_sat": 1}, intent_id="dup", path=p)


def test_second_outcome_for_one_intent_is_refused(tmp_path) -> None:
    p = tmp_path / "ops.jsonl"
    prepare_ln_intent("create_invoice", plan={"value_sat": 1}, intent_id="i1", path=p)
    assert append_ln_outcome("create_invoice", "executed", plan={}, intent_id="i1", path=p) is True
    assert append_ln_outcome("create_invoice", "error", plan={}, intent_id="i1", path=p) is False
    assert verify_ln_ops_ledger(p)["ok"] is True


# ---- BL-3 / M-12d: migration provenance, detailed skips, duplicate tolerance ----


def _legacy(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )


def test_migration_is_non_destructive_redacted_and_verified(tmp_path) -> None:
    source = tmp_path / "legacy.jsonl"
    destination = tmp_path / "v2.jsonl"
    invoice = "lnbc250u1migration-secret"
    _legacy(
        source,
        [
            {
                "ts": "2026-07-02T05:46:20+00:00",
                "action": "pay_invoice",
                "state": "error",
                "plan": {"payment_request": invoice},
                "response": {"payment_preimage": "44" * 32},
            }
        ],
    )
    original = source.read_bytes()
    report = migrate_legacy_ln_ops(source, destination)
    assert source.read_bytes() == original  # ADR-0016 invariant 1
    assert report["schema"] == "ln-ops-migration/v2"
    assert report["verification"]["ok"] is True
    assert report["verification"]["open_intents"] == []
    assert report["written_records"] == 2
    migrated = destination.read_text(encoding="utf-8")
    assert invoice not in migrated
    assert "44" * 32 not in migrated


def test_migration_marks_synthetic_intents_with_provenance(tmp_path) -> None:
    source = tmp_path / "legacy.jsonl"
    destination = tmp_path / "v2.jsonl"
    _legacy(
        source,
        [
            {
                "ts": "2026-07-02T05:46:20+00:00",
                "action": "keysend",
                "state": "executed",
                "plan": {"dest_pubkey_hex": "02-secret", "amt_sat": 7},
                "response": {},
            }
        ],
    )
    migrate_legacy_ln_ops(source, destination)
    intent, outcome = (
        json.loads(line) for line in destination.read_text(encoding="utf-8").splitlines()
    )
    assert intent["state"] == "intent"
    assert intent["migrated"] is True
    assert intent["synthetic_intent"] is True
    assert intent["source_line"] == 1
    # The outcome is REAL — it must carry provenance but never claim to be synthetic.
    assert outcome["migrated"] is True and outcome["source_line"] == 1
    assert "synthetic_intent" not in outcome


def test_migration_reports_every_skipped_line_in_detail(tmp_path) -> None:
    source = tmp_path / "legacy.jsonl"
    destination = tmp_path / "v2.jsonl"
    source.write_text(
        json.dumps({"ts": "t", "action": "pay_invoice", "state": "planned", "plan": {}})
        + "\nNOT JSON\n"
        + json.dumps([1, 2, 3])
        + "\n"
        + json.dumps({"ts": "t", "action": "keysend", "state": "disabled", "plan": {}})
        + "\n",
        encoding="utf-8",
    )
    report = migrate_legacy_ln_ops(source, destination)
    assert report["written_records"] == 0
    assert report["skipped_records"] == 4
    assert report["skipped"] == [
        {"line": 1, "reason": "non-terminal legacy state", "state": "planned"},
        {"line": 2, "reason": "unparseable json", "state": ""},
        {"line": 3, "reason": "not a json object", "state": ""},
        {"line": 4, "reason": "non-terminal legacy state", "state": "disabled"},
    ]


def test_migration_tolerates_duplicate_payment_hashes(tmp_path) -> None:
    # M-12d: the M-4 guard must not be applied to the historical replay — a repeated
    # invoice in the past is a fact to record, not a reason to abort the migration.
    source = tmp_path / "legacy.jsonl"
    destination = tmp_path / "v2.jsonl"
    row = {
        "ts": "2026-07-02T05:46:20+00:00",
        "action": "pay_invoice",
        "state": "executed",
        "plan": {"amount_sat": 1000, "payment_hash": "55" * 32},
        "response": {},
    }
    _legacy(source, [row, {**row, "ts": "2026-07-02T05:50:00+00:00"}])
    report = migrate_legacy_ln_ops(source, destination)
    assert report["written_records"] == 4
    assert report["skipped"] == []
    assert report["verification"]["ok"] is True
    intents = [
        json.loads(line)
        for line in destination.read_text(encoding="utf-8").splitlines()
        if json.loads(line)["state"] == "intent"
    ]
    assert [intent["source_line"] for intent in intents] == [1, 2]
    assert len({intent["intent_id"] for intent in intents}) == 2


def test_migration_refuses_to_overwrite_or_alias(tmp_path) -> None:
    source = tmp_path / "legacy.jsonl"
    _legacy(source, [{"ts": "t", "action": "keysend", "state": "executed", "plan": {}}])
    with pytest.raises(LightningOpsLedgerError, match="must differ from source"):
        migrate_legacy_ln_ops(source, source)
    destination = tmp_path / "v2.jsonl"
    destination.write_text("", encoding="utf-8")
    with pytest.raises(LightningOpsLedgerError, match="already exists"):
        migrate_legacy_ln_ops(source, destination)


# ---- Truth-chain binding ----


def test_ln_tip_attests_into_truth_chain_idempotently(tmp_path) -> None:
    ops = tmp_path / "ops.jsonl"
    truth = tmp_path / "truth.jsonl"
    prepare_ln_intent("create_invoice", plan={"value_sat": 10}, intent_id="i1", path=ops)
    append_ln_outcome(
        "create_invoice", "executed", plan={"value_sat": 10}, intent_id="i1", path=ops
    )
    first = attest_ln_ops_tip(ops_path=ops, truth_path=truth, mirror_audit=False)
    second = attest_ln_ops_tip(ops_path=ops, truth_path=truth, mirror_audit=False)
    assert first == {"total": 1, "attested": 1, "skipped": 0}
    assert second == {"total": 1, "attested": 0, "skipped": 1}
    assert verify_ledger(truth)["ok"] is True


def test_attest_refuses_an_invalid_ledger(tmp_path) -> None:
    ops = tmp_path / "ops.jsonl"
    truth = tmp_path / "truth.jsonl"
    ops.write_text(
        json.dumps({"ts": "t", "action": "keysend", "state": "executed"}) + "\n", encoding="utf-8"
    )
    with pytest.raises(LightningOpsLedgerError, match="refusing to attest"):
        attest_ln_ops_tip(ops_path=ops, truth_path=truth, mirror_audit=False)
    assert not truth.exists()  # a broken money journal never enters the truth chain


def test_attest_on_missing_ledger_is_a_noop(tmp_path) -> None:
    result = attest_ln_ops_tip(
        ops_path=tmp_path / "nope.jsonl", truth_path=tmp_path / "truth.jsonl", mirror_audit=False
    )
    assert result == {"total": 0, "attested": 0, "skipped": 0}
