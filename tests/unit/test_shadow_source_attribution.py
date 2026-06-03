"""NEO-P-002 (Weg B) delta over #137/#139/#140: taxonomy + resolver pre-filter.

Scope of this file is ONLY the Weg-B delta that is NOT already covered by:
  * #137/#140 canary + unattributed split  -> test_shadow_canary_attribution.py
  * #139 confidence guard + dedup counts    -> test_shadow_report_guards_instr.py

Covered here:
  * derive_autonomous_signal_source — ONE unified taxonomy (acceptance #6/#7)
  * resolve_pending skips canary/raw_scan/synthetic by default (acceptance #4)
  * include_canary is an explicit diagnostic opt-in (acceptance #5)
  * rr=2.0 is geometry, never read as signal quality (acceptance #3)
  * by_candidate_kind / by_score_source axes present
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.observability.shadow_candidate_ledger import (
    ShadowCandidate,
    build_shadow_report,
    record_candidate,
    resolve_pending,
)
from app.orchestrator.trading_loop import (
    SOURCE_AUTONOMOUS_GENERATOR,
    SOURCE_CANARY_PROBE,
    SOURCE_UNKNOWN,
    derive_autonomous_signal_source,
)

T0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)


# --- acceptance #6/#7: ONE taxonomy ---------------------------------------- #


def test_derive_source_canary_from_loop_control_docid() -> None:
    assert derive_autonomous_signal_source("loop_control_eth_bullish") == SOURCE_CANARY_PROBE


def test_derive_source_generator_from_real_docid() -> None:
    # acceptance #6: real generator -> autonomous_generator (unified with fills)
    assert derive_autonomous_signal_source("rss:doc-1234") == SOURCE_AUTONOMOUS_GENERATOR


def test_derive_source_unknown_when_empty() -> None:
    assert derive_autonomous_signal_source("") == SOURCE_UNKNOWN
    assert derive_autonomous_signal_source(None) == SOURCE_UNKNOWN


def test_autonomous_loop_is_never_a_produced_value() -> None:
    # acceptance #7: the helper must never emit the legacy "autonomous_loop".
    for doc in ("loop_control_x_y", "news:abc", "", None):
        assert derive_autonomous_signal_source(doc) != "autonomous_loop"


# --- acceptance #4/#5: resolver pre-filter --------------------------------- #


def _write(path: Path, **kw: object) -> None:
    c = ShadowCandidate.from_geometry(
        candidate_id=str(kw.pop("candidate_id", "c1")),
        ts_utc=str(kw.pop("ts_utc", T0.isoformat())),
        symbol="BTC/USDT",
        side="long",
        entry_price=100.0,
        stop_price=99.0,
        take_price=102.0,
        **kw,
    )
    record_candidate(c, path=path)


def _bars(symbol: str, start_ms: int, end_ms: int) -> list[tuple[int, float, float, float]]:
    base = int(T0.timestamp() * 1000)
    return [(base + 60_000, 101.0, 99.5, 100.5), (base + 3_600_000, 101.0, 99.0, 100.8)]


def test_resolver_skips_canary_by_default(tmp_path: Path) -> None:
    # acceptance #4: canary_probe rows are not resolved by default -> skipped_kind.
    ledger = tmp_path / "l.jsonl"
    resolved = tmp_path / "r.jsonl"
    _write(
        ledger,
        candidate_id="real",
        source="autonomous_generator",
        candidate_kind="signal_candidate",
    )
    _write(
        ledger,
        candidate_id="canary",
        source="canary_probe",
        candidate_kind="signal_candidate",
        is_canary=True,
    )
    counts = resolve_pending(
        fetch_klines=_bars,
        now=T0 + timedelta(hours=2),
        ledger_path=ledger,
        resolved_path=resolved,
    )
    assert counts["resolved"] == 1  # only the real row
    assert counts["skipped_kind"] == 1  # the canary row


def test_resolver_skips_raw_scan_kind(tmp_path: Path) -> None:
    # acceptance #4: non-resolvable candidate_kind is skipped by default.
    ledger = tmp_path / "l.jsonl"
    resolved = tmp_path / "r.jsonl"
    _write(ledger, candidate_id="scan", source="autonomous_generator", candidate_kind="raw_scan")
    counts = resolve_pending(
        fetch_klines=_bars, now=T0 + timedelta(hours=2), ledger_path=ledger, resolved_path=resolved
    )
    assert counts["resolved"] == 0
    assert counts["skipped_kind"] == 1


def test_resolver_skips_synthetic_default(tmp_path: Path) -> None:
    ledger = tmp_path / "l.jsonl"
    resolved = tmp_path / "r.jsonl"
    _write(
        ledger,
        candidate_id="syn",
        source="autonomous_generator",
        candidate_kind="signal_candidate",
        is_synthetic_default=True,
    )
    counts = resolve_pending(
        fetch_klines=_bars, now=T0 + timedelta(hours=2), ledger_path=ledger, resolved_path=resolved
    )
    assert counts["resolved"] == 0
    assert counts["skipped_kind"] == 1


def test_include_canary_is_explicit_opt_in(tmp_path: Path) -> None:
    # acceptance #5: include_canary=True resolves canary rows too (diagnostic).
    ledger = tmp_path / "l.jsonl"
    resolved = tmp_path / "r.jsonl"
    _write(
        ledger,
        candidate_id="canary",
        source="canary_probe",
        candidate_kind="signal_candidate",
        is_canary=True,
    )
    counts = resolve_pending(
        fetch_klines=_bars,
        now=T0 + timedelta(hours=2),
        ledger_path=ledger,
        resolved_path=resolved,
        include_canary=True,
    )
    assert counts["resolved"] == 1
    assert counts["skipped_kind"] == 0


def test_legacy_candidate_kind_none_still_resolvable(tmp_path: Path) -> None:
    # legacy rows (candidate_kind None) stay resolvable for backward-compat, as
    # long as their source is not canary_probe (acceptance #4 boundary).
    ledger = tmp_path / "l.jsonl"
    resolved = tmp_path / "r.jsonl"
    _write(ledger, candidate_id="legacy", source="autonomous_loop")  # no candidate_kind
    counts = resolve_pending(
        fetch_klines=_bars, now=T0 + timedelta(hours=2), ledger_path=ledger, resolved_path=resolved
    )
    assert counts["resolved"] == 1
    assert counts["skipped_kind"] == 0


# --- acceptance #3 + attribution axes -------------------------------------- #


def _v2_real(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "symbol": "ETH/USDT",
        "side": "long",
        "source": "autonomous_generator",
        "candidate_kind": "signal_candidate",
        "score_source": "unknown",
        "schema_version": "v2",
        "stop_dist_bps": 50.0,
        "take_dist_bps": 100.0,  # rr = 2.0 — GEOMETRY, not signal quality
        "gate_would_reject": False,
        "mae_bps": -40.0,
        "mfe_bps": 60.0,
        "mfe_before_mae": True,
        "reached_take": False,
        "reached_stop": False,
        "fwd_300s_bps": 5.0,
        "fwd_3600s_bps": 10.0,
    }
    base.update(over)
    return base


def test_rr_two_does_not_become_a_confidence_signal() -> None:
    # acceptance #3: rr=2.0 is the geometry default. With constant confidence the
    # #139 guard flags NON_INFORMATIVE_CONSTANT_FEATURE and primary_class still
    # derives purely from MAE/MFE (here INSUFFICIENT at n<20).
    rows = [_v2_real(signal_confidence=0.85) for _ in range(5)]
    rep = build_shadow_report(rows, total_candidates=5)
    assert rep["confidence_analysis_status"] == "NON_INFORMATIVE_CONSTANT_FEATURE"
    assert rep["confidence_buckets_enabled"] is False
    assert rep["primary_class"] == "INSUFFICIENT_DATA"  # n<20, MAE/MFE-based


def test_attribution_axes_present() -> None:
    rows = [_v2_real(signal_confidence=0.85) for _ in range(3)]
    rep = build_shadow_report(rows, total_candidates=3)
    assert isinstance(rep["by_candidate_kind"], dict)
    assert "signal_candidate" in rep["by_candidate_kind"]
    assert isinstance(rep["by_score_source"], dict)
    assert "unknown" in rep["by_score_source"]
