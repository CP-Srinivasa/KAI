"""Pre-registration resolution (verdict half of the falsification loop) tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.observability.edge_validation_gate import EdgeValidationVerdict, evaluate_edge_validation
from app.research.prereg_ledger import canonical_edge_claim, canonical_edge_prereg_id
from app.research.prereg_resolution import (
    VERDICT_INSUFFICIENT_N,
    VERDICT_MET,
    VERDICT_NOT_MET,
    Resolution,
    ResolutionLedger,
    manual_resolution,
    render_resolution,
    resolve_canonical,
)

_CLAIM = canonical_edge_claim(min_n=100, confidence=0.95)  # sample_size_target == 100
_PID = canonical_edge_prereg_id(min_n=100, confidence=0.95)
_NOW = "2026-07-01T12:00:00+00:00"


def _verdict(*, ready: bool, n: int, mean: float, dsr: float | None) -> EdgeValidationVerdict:
    return EdgeValidationVerdict(
        ready=ready,
        trade_count=n,
        trials=5,
        sharpe=None,
        psr_zero=None,
        deflated_sharpe=dsr,
        min_trl=None,
        mean_net_bps=mean,
    )


def _resolve(v: EdgeValidationVerdict) -> Resolution:
    return resolve_canonical(prereg_id=_PID, claim=_CLAIM, verdict=v, resolved_at_utc=_NOW)


def test_met_when_sample_reached_and_gate_ready() -> None:
    res = _resolve(_verdict(ready=True, n=150, mean=12.0, dsr=0.97))
    assert res.verdict == VERDICT_MET
    assert res.measured_n == 150
    assert res.sample_size_target == 100
    assert res.ready is True
    assert res.prereg_id == _PID
    assert res.name == "canonical_edge"


def test_not_met_when_mean_non_positive_regardless_of_n() -> None:
    # The canonical edge as of 2026-07-01: n below target but mean strongly
    # negative → the 'net>0' claim is contradicted, not merely under-sampled.
    res = _resolve(_verdict(ready=False, n=68, mean=-29.4, dsr=None))
    assert res.verdict == VERDICT_NOT_MET
    assert res.mean_net_bps == -29.4
    assert "contradicts" in res.reason


def test_insufficient_n_when_positive_but_under_sampled() -> None:
    res = _resolve(_verdict(ready=False, n=50, mean=5.0, dsr=None))
    assert res.verdict == VERDICT_INSUFFICIENT_N
    assert "under-sampled" in res.reason


def test_not_met_when_sample_reached_but_bar_not_cleared() -> None:
    # Enough data, positive mean, but the statistical bar (DSR) was not cleared.
    res = _resolve(_verdict(ready=False, n=120, mean=3.0, dsr=0.40))
    assert res.verdict == VERDICT_NOT_MET
    assert "not established" in res.reason


def test_integration_negative_series_through_the_real_gate() -> None:
    # Feed a genuinely negative cost-net series through the real edge gate and
    # resolve it — the loop must falsify (NOT_MET), never silently pass.
    net_bps = [-50.0, -40.0, -60.0, -55.0, -45.0] * 24  # n=120, mean < 0
    verdict = evaluate_edge_validation(net_bps, trials=5, min_n=100, confidence=0.95)
    res = _resolve(verdict)
    assert res.verdict == VERDICT_NOT_MET
    assert res.measured_n == 120
    assert res.mean_net_bps < 0


def test_manual_resolution_records_operator_verdict() -> None:
    res = manual_resolution(
        prereg_id="deadbeefdeadbeef",
        name="funding_premium_meanrev_1h",
        verdict="not_met",
        note="funding n758 trust 0.5 shadow — no promote",
        resolved_at_utc=_NOW,
    )
    assert res.verdict == VERDICT_NOT_MET
    assert res.source == "manual"
    assert res.reason.startswith("funding")


def test_manual_resolution_rejects_bad_verdict() -> None:
    with pytest.raises(ValueError, match="verdict must be one of"):
        manual_resolution(prereg_id="x", name="y", verdict="maybe", note="", resolved_at_utc=_NOW)


def test_resolution_json_roundtrip() -> None:
    res = _resolve(_verdict(ready=False, n=68, mean=-29.4, dsr=None))
    restored = Resolution.from_dict(__import__("json").loads(res.to_json()))
    assert restored == res


def test_ledger_record_read_and_latest(tmp_path: Path) -> None:
    ledger = ResolutionLedger(tmp_path / "prereg_verdicts.jsonl")
    assert ledger.entries() == []
    assert ledger.latest(_PID) is None

    first = _resolve(_verdict(ready=False, n=50, mean=5.0, dsr=None))
    second = _resolve(_verdict(ready=False, n=68, mean=-29.4, dsr=None))
    ledger.record(first)
    ledger.record(second)

    entries = ledger.entries()
    assert len(entries) == 2
    # append-only → last recorded wins for the same prereg id
    assert ledger.latest(_PID) == second
    assert ledger.latest("never-seen") is None


def test_ledger_skips_corrupt_line(tmp_path: Path) -> None:
    path = tmp_path / "prereg_verdicts.jsonl"
    ledger = ResolutionLedger(path)
    ledger.record(_resolve(_verdict(ready=True, n=150, mean=12.0, dsr=0.97)))
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{not valid json\n")
        fh.write("\n")
    assert len(ledger.entries()) == 1


def test_render_is_human_readable() -> None:
    text = render_resolution(_resolve(_verdict(ready=False, n=68, mean=-29.4, dsr=None)))
    assert "PREREG RESOLUTION: NOT_MET" in text
    assert "canonical_edge" in text
    assert _PID in text
