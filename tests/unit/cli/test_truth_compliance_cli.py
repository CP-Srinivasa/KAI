"""Operator CLI for truth attestation, compliance export and capital snapshot.

Closes the usability gap of the ADR-0013 bundles: everything built in
#535/#538/#539 becomes reachable via ``trading ...`` without writing Python.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from app.cli.main import app
from app.research.prereg_ledger import PreRegistrationLedger, register

runner = CliRunner()


def _register_one(prereg_path) -> None:
    PreRegistrationLedger(prereg_path).record(
        register(
            name="hyp_cli",
            direction="long",
            horizon="24h",
            success_criteria="P>=0.95 net positive",
            sample_size_target=100,
            created_at_utc="2026-07-01T00:00:00+00:00",
        )
    )


def test_provenance_record_then_export_roundtrip(tmp_path) -> None:
    ledger = tmp_path / "provenance.jsonl"
    rec = runner.invoke(
        app,
        [
            "trading",
            "provenance-record",
            "--kind",
            "ownership_proof",
            "--wallet",
            "bc1qself",
            "--method",
            "satoshi_test",
            "--ledger-path",
            str(ledger),
        ],
    )
    assert rec.exit_code == 0, rec.output
    out_file = tmp_path / "export.json"
    exp = runner.invoke(
        app,
        [
            "trading",
            "compliance-export",
            "--ledger-path",
            str(ledger),
            "--out",
            str(out_file),
        ],
    )
    assert exp.exit_code == 0, exp.output
    export = json.loads(out_file.read_text(encoding="utf-8"))
    assert export["wallets"]["bc1qself"]["has_ownership_proof"] is True
    assert export["gaps"] == []


def test_truth_attest_prereg_then_verify_green(tmp_path) -> None:
    prereg = tmp_path / "prereg.jsonl"
    truth = tmp_path / "truth.jsonl"
    _register_one(prereg)
    att = runner.invoke(
        app,
        [
            "trading",
            "truth-attest-prereg",
            "--prereg-path",
            str(prereg),
            "--ledger-path",
            str(truth),
            "--no-audit-mirror",
        ],
    )
    assert att.exit_code == 0, att.output
    ver = runner.invoke(app, ["trading", "truth-verify", "--ledger-path", str(truth)])
    assert ver.exit_code == 0, ver.output
    assert '"ok": true' in ver.output


def test_truth_verify_exits_nonzero_on_tamper(tmp_path) -> None:
    prereg = tmp_path / "prereg.jsonl"
    truth = tmp_path / "truth.jsonl"
    _register_one(prereg)
    runner.invoke(
        app,
        [
            "trading",
            "truth-attest-prereg",
            "--prereg-path",
            str(prereg),
            "--ledger-path",
            str(truth),
            "--no-audit-mirror",
        ],
    )
    doc = json.loads(truth.read_text(encoding="utf-8").splitlines()[0])
    doc["payload"]["name"] = "umgeschrieben"
    truth.write_text(json.dumps(doc, sort_keys=True) + "\n", encoding="utf-8")
    ver = runner.invoke(app, ["trading", "truth-verify", "--ledger-path", str(truth)])
    assert ver.exit_code == 1


def test_capital_snapshot_with_recommendation(tmp_path) -> None:
    res = runner.invoke(
        app,
        [
            "trading",
            "capital-snapshot",
            "--balances",
            '{"operating": 6000, "reserve": 3000, "long_term": 1000}',
            "--gain",
            "1000",
            "--split",
            "0.5",
            "--reserve-target",
            "10000",
        ],
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["snapshot"]["total"] == 10000.0
    assert payload["recommendation"]["to_reserve_usd"] == 500.0
    assert payload["recommendation"]["executes"] is False
