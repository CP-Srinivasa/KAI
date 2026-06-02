"""Risk-gate audit recorder + report (staged-rollout safety net)."""

from __future__ import annotations

import json
from pathlib import Path

from app.observability.risk_gate_audit import (
    build_risk_gate_audit_report,
    record_risk_gate_eval,
)
from app.risk.models import RiskCheckResult


def _result(*, would_reject: bool, codes: list[str], mode: str) -> RiskCheckResult:
    return RiskCheckResult(
        approved=not would_reject,
        check_id="rck_test",
        timestamp_utc="2026-06-02T12:00:00+00:00",
        symbol="US/USDT",
        check_type="pre_order",
        reason="test",
        would_reject=would_reject,
        would_reject_violations=["leveraged_risk_too_high:42%>35%"] if would_reject else [],
        would_reject_codes=codes,
        details={"gates_mode": mode, "signal_geometry": {"rr_t1": 0.11}},
    )


def test_record_only_writes_when_flagged(tmp_path: Path) -> None:
    log = tmp_path / "risk_gate_audit.jsonl"
    # not flagged -> no write
    assert (
        record_risk_gate_eval(
            risk_result=_result(would_reject=False, codes=[], mode="audit"),
            log_path=log,
        )
        is False
    )
    assert not log.exists()
    # flagged -> write
    assert (
        record_risk_gate_eval(
            risk_result=_result(would_reject=True, codes=["REJECT_RISK_TOO_HIGH"], mode="audit"),
            envelope_id="ENV-X",
            source="telegram_premium_channel_approved",
            symbol="US/USDT",
            log_path=log,
        )
        is True
    )
    rec = json.loads(log.read_text(encoding="utf-8").strip())
    assert rec["event"] == "risk_gate_audit"
    assert rec["would_reject"] is True
    assert rec["gates_mode"] == "audit"
    assert rec["enforced"] is False
    assert rec["would_reject_codes"] == ["REJECT_RISK_TOO_HIGH"]


def test_report_aggregates_distribution(tmp_path: Path) -> None:
    log = tmp_path / "risk_gate_audit.jsonl"
    for codes, sym, src, enforced in [
        (["REJECT_RISK_TOO_HIGH"], "US/USDT", "ch_a", False),
        (["REJECT_RR_TOO_LOW"], "US/USDT", "ch_a", False),
        (["REJECT_RISK_TOO_HIGH", "REJECT_RR_TOO_LOW"], "DOGE/USDT", "ch_b", True),
    ]:
        rec = {
            "event": "risk_gate_audit",
            "symbol": sym,
            "source": src,
            "enforced": enforced,
            "would_reject": True,
            "would_reject_codes": codes,
        }
        with log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")

    report = build_risk_gate_audit_report(log_path=log)
    assert report.total_records == 3
    assert report.would_reject_count == 3
    assert report.reject_rate == 1.0
    assert report.reason_code_distribution["REJECT_RISK_TOO_HIGH"] == 2
    assert report.reason_code_distribution["REJECT_RR_TOO_LOW"] == 2
    assert report.rejected_by_symbol["US/USDT"] == 2
    assert report.rejected_by_source["ch_b"] == 1
    assert report.enforced_count == 1


def test_report_missing_file_is_safe() -> None:
    report = build_risk_gate_audit_report(log_path="does/not/exist.jsonl")
    assert report.total_records == 0
    assert "no audit file yet" in report.notes
