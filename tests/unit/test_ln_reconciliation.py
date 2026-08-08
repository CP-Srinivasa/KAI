from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.lightning.client import LightningUnavailableError, LndPayment, LndPaymentPage
from app.lightning.ops_ledger import (
    append_ln_outcome,
    attest_ln_ops_tip,
    prepare_ln_intent,
    read_verified_ln_ops_snapshot,
    verify_ln_ops_ledger,
)
from app.lightning.reconciliation import reconcile_ln_ops
from app.truth.ledger import append_attestation

PAYMENT_HASH = "ab" * 32


class FakePaymentsClient:
    def __init__(self, *pages: LndPaymentPage | Exception) -> None:
        self.pages = list(pages)
        self.calls: list[dict[str, object]] = []

    async def list_payments(self, **kwargs: object) -> LndPaymentPage:
        self.calls.append(kwargs)
        if not self.pages:
            raise AssertionError("unexpected list_payments call")
        page = self.pages.pop(0)
        if isinstance(page, Exception):
            raise page
        return page


class MutatingPaymentsClient(FakePaymentsClient):
    def __init__(self, ops_path: Path, replacement: str, *pages: LndPaymentPage) -> None:
        super().__init__(*pages)
        self.ops_path = ops_path
        self.replacement = replacement

    async def list_payments(self, **kwargs: object) -> LndPaymentPage:
        page = await super().list_payments(**kwargs)
        self.ops_path.write_text(self.replacement, encoding="utf-8")
        return page


def _page(
    *payments: LndPayment,
    first: int = 1,
    last: int = 1,
) -> LndPaymentPage:
    return LndPaymentPage(
        payments=payments,
        first_index_offset=first,
        last_index_offset=last,
        reversed=False,
    )


def _payment(
    status: str,
    *,
    payment_hash: str = PAYMENT_HASH,
    value_sat: int = 21,
    fee_sat: int = 1,
    failure_reason: str = "",
    payment_index: int = 1,
) -> LndPayment:
    return LndPayment(
        payment_hash=payment_hash,
        status=status,
        failure_reason=failure_reason,
        value_sat=value_sat,
        fee_sat=fee_sat,
        payment_index=payment_index,
    )


def _open_invoice(ops_path: Path, *, intent_id: str = "intent-1") -> dict[str, object]:
    return prepare_ln_intent(
        "pay_invoice",
        intent_id=intent_id,
        plan={
            "amount_sat": 21,
            "fee_limit_sat": 2,
            "payment_hash": PAYMENT_HASH,
            "payment_request_hash": "cd" * 32,
        },
        path=ops_path,
    )


def _attest(ops_path: Path, truth_path: Path) -> None:
    result = attest_ln_ops_tip(
        ops_path=ops_path,
        truth_path=truth_path,
        mirror_audit=False,
    )
    assert result["attested"] == 1


def _read_report(path: Path) -> dict[str, object]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    return rows[0]


def test_verified_ops_snapshot_is_atomic_fail_closed_and_contains_rows(tmp_path: Path) -> None:
    missing = read_verified_ln_ops_snapshot(tmp_path / "missing.jsonl")
    assert missing["ok"] is False
    assert missing["records"] == []

    corrupt = tmp_path / "corrupt.jsonl"
    corrupt.write_text("{torn", encoding="utf-8")
    broken = read_verified_ln_ops_snapshot(corrupt)
    assert broken["ok"] is False
    assert broken["records"] == []

    valid = tmp_path / "valid.jsonl"
    row = _open_invoice(valid)
    snapshot = read_verified_ln_ops_snapshot(valid)
    assert snapshot["ok"] is True
    assert snapshot["checked"] == 1
    assert snapshot["open_intents"] == ["intent-1"]
    assert snapshot["records"] == [row]


@pytest.mark.asyncio
async def test_no_open_intents_checks_truth_tip_without_touching_node(tmp_path: Path) -> None:
    ops = tmp_path / "ops.jsonl"
    truth = tmp_path / "truth.jsonl"
    report = tmp_path / "reports.jsonl"
    intent = _open_invoice(ops)
    assert append_ln_outcome(
        "pay_invoice",
        "executed",
        intent_id=str(intent["intent_id"]),
        plan=dict(intent["plan"]),
        response={"payment_hash": PAYMENT_HASH, "status": "SUCCEEDED", "amount_sat": 21},
        path=ops,
    )
    _attest(ops, truth)
    client = FakePaymentsClient()

    result = await reconcile_ln_ops(
        client=client,
        ops_path=ops,
        truth_path=truth,
        report_path=report,
        page_size=2,
    )

    assert result["status"] == "ok"
    assert result["tip_cross_check"]["contained"] is True
    assert result["node"]["skipped"] == "no_open_intents"
    assert client.calls == []


@pytest.mark.asyncio
async def test_succeeded_payment_closes_open_intent_after_complete_scan(tmp_path: Path) -> None:
    ops = tmp_path / "ops.jsonl"
    truth = tmp_path / "truth.jsonl"
    report = tmp_path / "reports.jsonl"
    _open_invoice(ops)
    _attest(ops, truth)
    client = FakePaymentsClient(_page(_payment("SUCCEEDED")))

    result = await reconcile_ln_ops(
        client=client,
        ops_path=ops,
        truth_path=truth,
        report_path=report,
        page_size=2,
    )

    assert result["status"] == "ok"
    assert result["intents"][0]["result"] == "journalled_executed"
    assert verify_ln_ops_ledger(ops)["open_intents"] == []
    rows = read_verified_ln_ops_snapshot(ops)["records"]
    assert rows[-1]["state"] == "executed"
    assert rows[-1]["response"] == {
        "amount_sat": 21,
        "fee_sat": 1,
        "payment_hash": PAYMENT_HASH,
        "status": "SUCCEEDED",
    }


@pytest.mark.asyncio
async def test_attested_tip_may_be_an_older_record_contained_in_extended_chain(
    tmp_path: Path,
) -> None:
    ops = tmp_path / "ops.jsonl"
    truth = tmp_path / "truth.jsonl"
    first = _open_invoice(ops, intent_id="already-done")
    assert append_ln_outcome(
        "pay_invoice",
        "executed",
        intent_id="already-done",
        plan=dict(first["plan"]),
        response={"payment_hash": PAYMENT_HASH, "status": "SUCCEEDED", "amount_sat": 21},
        path=ops,
    )
    _attest(ops, truth)
    second_hash = "ef" * 32
    prepare_ln_intent(
        "pay_invoice",
        intent_id="new-open",
        plan={"amount_sat": 8, "payment_hash": second_hash},
        path=ops,
    )

    result = await reconcile_ln_ops(
        client=FakePaymentsClient(
            _page(_payment("SUCCEEDED", payment_hash=second_hash, value_sat=8))
        ),
        ops_path=ops,
        truth_path=truth,
        report_path=tmp_path / "report.jsonl",
        page_size=2,
    )

    assert result["status"] == "ok"
    assert result["tip_cross_check"]["contained"] is True
    assert result["tip_cross_check"]["journal_seq"] == 2
    assert verify_ln_ops_ledger(ops)["open_intents"] == []


@pytest.mark.asyncio
async def test_failed_payment_closes_as_error_with_allowlisted_reason(tmp_path: Path) -> None:
    ops = tmp_path / "ops.jsonl"
    truth = tmp_path / "truth.jsonl"
    report = tmp_path / "reports.jsonl"
    _open_invoice(ops)
    _attest(ops, truth)
    client = FakePaymentsClient(_page(_payment("FAILED", failure_reason="FAILURE_REASON_NO_ROUTE")))

    result = await reconcile_ln_ops(
        client=client,
        ops_path=ops,
        truth_path=truth,
        report_path=report,
        page_size=2,
    )

    assert result["status"] == "ok"
    assert result["intents"][0]["result"] == "journalled_error"
    rows = read_verified_ln_ops_snapshot(ops)["records"]
    assert rows[-1]["state"] == "error"
    assert rows[-1]["response"]["failure_reason"] == "FAILURE_REASON_NO_ROUTE"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["UNKNOWN", "IN_FLIGHT", "INITIATED"])
async def test_nonterminal_node_status_never_closes_intent(tmp_path: Path, status: str) -> None:
    ops = tmp_path / f"ops-{status}.jsonl"
    truth = tmp_path / f"truth-{status}.jsonl"
    report = tmp_path / f"report-{status}.jsonl"
    _open_invoice(ops)
    _attest(ops, truth)

    result = await reconcile_ln_ops(
        client=FakePaymentsClient(_page(_payment(status))),
        ops_path=ops,
        truth_path=truth,
        report_path=report,
        page_size=2,
    )

    assert result["status"] == "attention"
    assert result["intents"][0]["result"] == "left_open"
    assert verify_ln_ops_ledger(ops)["open_intents"] == ["intent-1"]


@pytest.mark.asyncio
async def test_unmatched_unsupported_ambiguous_and_amount_mismatch_remain_open(
    tmp_path: Path,
) -> None:
    cases: list[tuple[str, list[LndPayment], str]] = [
        ("unmatched", [], "payment_not_found"),
        (
            "ambiguous",
            [_payment("SUCCEEDED", payment_index=1), _payment("SUCCEEDED", payment_index=2)],
            "ambiguous_payment_hash",
        ),
        ("mismatch", [_payment("SUCCEEDED", value_sat=22)], "amount_mismatch"),
    ]
    for name, payments, reason in cases:
        ops = tmp_path / f"{name}-ops.jsonl"
        truth = tmp_path / f"{name}-truth.jsonl"
        report = tmp_path / f"{name}-report.jsonl"
        _open_invoice(ops)
        _attest(ops, truth)
        result = await reconcile_ln_ops(
            client=FakePaymentsClient(_page(*payments)),
            ops_path=ops,
            truth_path=truth,
            report_path=report,
            page_size=3,
        )
        assert result["status"] == "attention"
        assert result["intents"][0]["reason"] == reason
        assert verify_ln_ops_ledger(ops)["open_intents"] == ["intent-1"]

    unsupported_ops = tmp_path / "unsupported-ops.jsonl"
    unsupported_truth = tmp_path / "unsupported-truth.jsonl"
    prepare_ln_intent(
        "keysend",
        intent_id="keysend-1",
        plan={"amount_sat": 21, "recipient_hash": "ef" * 32},
        path=unsupported_ops,
    )
    _attest(unsupported_ops, unsupported_truth)
    client = FakePaymentsClient(_page())
    result = await reconcile_ln_ops(
        client=client,
        ops_path=unsupported_ops,
        truth_path=unsupported_truth,
        report_path=tmp_path / "unsupported-report.jsonl",
        page_size=3,
    )
    assert result["status"] == "attention"
    assert result["intents"][0]["reason"] == "unsupported_action"
    assert client.calls == []


@pytest.mark.asyncio
async def test_broken_missing_or_rolled_back_truth_tip_stops_before_node_and_write(
    tmp_path: Path,
) -> None:
    for name in ("missing", "wrong", "corrupt"):
        ops = tmp_path / f"{name}-ops.jsonl"
        truth = tmp_path / f"{name}-truth.jsonl"
        report = tmp_path / f"{name}-report.jsonl"
        _open_invoice(ops)
        if name == "wrong":
            wrong_hash = "ff" * 32
            append_attestation(
                "lightning_ops_tip",
                f"ln-ops-tip:{wrong_hash}",
                {"schema": "ln-ops-tip/v1", "record_hash": wrong_hash, "seq": 1},
                path=truth,
                mirror_audit=False,
            )
        elif name == "corrupt":
            truth.write_text("{torn", encoding="utf-8")
        client = FakePaymentsClient(_page(_payment("SUCCEEDED")))
        result = await reconcile_ln_ops(
            client=client,
            ops_path=ops,
            truth_path=truth,
            report_path=report,
            page_size=2,
        )
        assert result["status"] == "error"
        assert result["tip_cross_check"]["contained"] is False
        assert client.calls == []
        assert verify_ln_ops_ledger(ops)["open_intents"] == ["intent-1"]


@pytest.mark.asyncio
async def test_partial_or_stalled_pagination_never_writes_an_outcome(tmp_path: Path) -> None:
    for name, second in (
        ("transport", LightningUnavailableError("node unavailable")),
        ("stalled", _page(_payment("SUCCEEDED"), first=1, last=1)),
    ):
        ops = tmp_path / f"{name}-ops.jsonl"
        truth = tmp_path / f"{name}-truth.jsonl"
        report = tmp_path / f"{name}-report.jsonl"
        _open_invoice(ops)
        _attest(ops, truth)
        first = _page(_payment("SUCCEEDED"), first=1, last=1)
        result = await reconcile_ln_ops(
            client=FakePaymentsClient(first, second),
            ops_path=ops,
            truth_path=truth,
            report_path=report,
            page_size=1,
        )
        assert result["status"] == "error"
        assert verify_ln_ops_ledger(ops)["open_intents"] == ["intent-1"]


@pytest.mark.asyncio
async def test_journal_truncation_during_node_scan_is_caught_before_append(tmp_path: Path) -> None:
    ops = tmp_path / "ops.jsonl"
    truth = tmp_path / "truth.jsonl"
    _open_invoice(ops)
    _attest(ops, truth)
    original = ops.read_text(encoding="utf-8")
    client = MutatingPaymentsClient(ops, "", _page(_payment("SUCCEEDED")))

    result = await reconcile_ln_ops(
        client=client,
        ops_path=ops,
        truth_path=truth,
        report_path=tmp_path / "report.jsonl",
        page_size=2,
    )

    assert result["status"] == "error"
    assert result["tip_cross_check"]["reason"] == "attested_tip_not_in_journal"
    assert ops.read_text(encoding="utf-8") == ""
    assert original


@pytest.mark.asyncio
async def test_report_is_redacted_and_durably_appended(tmp_path: Path) -> None:
    ops = tmp_path / "ops.jsonl"
    truth = tmp_path / "truth.jsonl"
    report = tmp_path / "reports.jsonl"
    _open_invoice(ops)
    _attest(ops, truth)
    secret = "lnbc1this-must-not-survive"

    result = await reconcile_ln_ops(
        client=FakePaymentsClient(_page(_payment("FAILED", failure_reason=secret))),
        ops_path=ops,
        truth_path=truth,
        report_path=report,
        page_size=2,
    )

    persisted = _read_report(report)
    assert persisted == result
    encoded = report.read_text(encoding="utf-8")
    assert secret not in encoded
    assert "payment_request" not in encoded
    assert "preimage" not in encoded
    assert "route" not in encoded
    assert "hop" not in encoded


def test_reconciliation_units_are_install_only_timer_decoupled_and_not_boot_path() -> None:
    root = Path(__file__).resolve().parents[2]
    installer = (root / "scripts/pi_install_systemd.sh").read_text(encoding="utf-8")
    units = installer.split("UNITS=(", 1)[1].split(")", 1)[0]
    enabled = installer.split("ENABLE_ON_INSTALL=(", 1)[1].split(")", 1)[0]
    for name in ("kai-ln-reconcile.service", "kai-ln-reconcile.timer"):
        assert name in units
        assert name not in enabled

    service = (root / "deploy/systemd/kai-ln-reconcile.service").read_text(encoding="utf-8")
    timer = (root / "deploy/systemd/kai-ln-reconcile.timer").read_text(encoding="utf-8")
    assert "Requires=" not in service + timer
    assert "scripts/ln_reconcile.py" in service
    assert "OnCalendar=" in timer
    assert "Persistent=true" not in timer
    assert "OnBootSec=" not in timer


def test_reconciliation_has_an_open_non_alpha_hypothesis_family() -> None:
    from app.research.hypothesis_families import OPEN, get_family

    family = get_family("money_path_integrity")
    assert family is not None
    assert family.status == OPEN
    assert family.constructions_failed == 0
    assert "safety" in family.notes.lower()


@pytest.mark.asyncio
async def test_cli_builds_only_the_read_client_and_fails_loud_on_attention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.ln_reconcile as cli

    scopes: list[str] = []
    read_client = object()

    def build_client(_cfg: object, *, credential_scope: str) -> object:
        scopes.append(credential_scope)
        return read_client

    async def reconcile(**kwargs: object) -> dict[str, object]:
        assert kwargs["client"] is read_client
        return {"status": "attention"}

    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: SimpleNamespace(lightning=SimpleNamespace(enabled=True)),
    )
    monkeypatch.setattr(cli, "_build_client", build_client)
    monkeypatch.setattr(cli, "reconcile_ln_ops", reconcile)

    assert await cli._main() == 1
    assert scopes == ["read"]
