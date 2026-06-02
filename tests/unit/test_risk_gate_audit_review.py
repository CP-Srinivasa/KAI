"""Risk-gate audit-window review — status staging + read-only invariants."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.observability.risk_gate_audit_review import build_review, render_telegram


def _bridge(path: Path, n_evaluated: int) -> None:
    """Write `n_evaluated` distinct envelopes whose stage is post-gate."""
    rows = []
    for i in range(n_evaluated):
        rows.append({"envelope_id": f"ENV-{i}", "stage": "rejected_risk"})
    # plus a pre-gate skip that must NOT count toward the denominator
    rows.append({"envelope_id": "ENV-skip", "stage": "skipped_source"})
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _audit(path: Path, codes_per_record: list[list[str]]) -> None:
    rows = [
        {
            "event": "risk_gate_audit",
            "would_reject": True,
            "would_reject_codes": c,
            "symbol": "US/USDT",
            "source": "telegram_premium_channel_approved",
        }
        for c in codes_per_record
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


@pytest.mark.parametrize(
    "n,expected",
    [(0, "NO_DATA"), (5, "INSUFFICIENT_DATA"), (15, "LOW_SAMPLE"), (30, "REVIEWABLE")],
)
def test_status_staging(tmp_path: Path, n: int, expected: str) -> None:
    bridge = tmp_path / "bridge.jsonl"
    audit = tmp_path / "audit.jsonl"
    _bridge(bridge, n)
    _audit(audit, [["REJECT_RISK_TOO_HIGH"]] * min(n, 3))
    v = build_review(audit_log_path=audit, bridge_path=bridge)
    assert v.status == expected
    # enforce_ready is ALWAYS False — never an automatic green light.
    assert v.enforce_ready is False
    assert "enforce" in v.decision.lower()


def test_reject_rate_uses_bridge_denominator(tmp_path: Path) -> None:
    bridge = tmp_path / "bridge.jsonl"
    audit = tmp_path / "audit.jsonl"
    _bridge(bridge, 40)  # 40 evaluated
    _audit(audit, [["REJECT_RISK_TOO_HIGH"], ["REJECT_RR_TOO_LOW"]])  # 2 flagged
    v = build_review(audit_log_path=audit, bridge_path=bridge)
    assert v.n_evaluated == 40
    assert v.would_reject_count == 2
    assert v.reject_rate == pytest.approx(0.05, abs=1e-6)
    assert v.status == "REVIEWABLE"


def test_no_data_is_not_an_error(tmp_path: Path) -> None:
    v = build_review(
        audit_log_path=tmp_path / "missing.jsonl",
        bridge_path=tmp_path / "missing_bridge.jsonl",
    )
    assert v.status == "NO_DATA"
    assert v.reject_rate is None
    assert v.enforce_ready is False


def test_preconditions_and_warnings(tmp_path: Path) -> None:
    bridge = tmp_path / "bridge.jsonl"
    audit = tmp_path / "audit.jsonl"
    _bridge(bridge, 1)
    _audit(audit, [["REJECT_RISK_TOO_HIGH"]])
    v = build_review(
        entry_mode="paper",
        gates_mode="enforce",
        max_leveraged_risk_pct=35.0,
        min_rr=0.5,
        audit_log_path=audit,
        bridge_path=bridge,
    )
    assert v.preconditions["entry_mode_disabled"] is False
    assert v.preconditions["gates_mode_audit"] is False
    # both deviations surfaced as notes
    assert any("gates_mode=enforce" in n for n in v.notes)
    assert any("entry_mode=paper" in n for n in v.notes)


def test_telegram_digest_is_short_and_safe(tmp_path: Path) -> None:
    bridge = tmp_path / "bridge.jsonl"
    audit = tmp_path / "audit.jsonl"
    _bridge(bridge, 12)
    _audit(audit, [["REJECT_RISK_TOO_HIGH"]] * 3)
    v = build_review(audit_log_path=audit, bridge_path=bridge)
    msg = render_telegram(v)
    assert "enforce-ready: NO" in msg
    assert "entry_mode stays disabled" in msg
    assert len(msg.splitlines()) <= 8  # concise, no dumps
