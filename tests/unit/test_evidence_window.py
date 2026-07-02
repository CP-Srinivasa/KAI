"""Evidence-Window report (Goal 2026-06-01, AUFGABE 1) — behaviour spec.

The Evidence Window joins the TWO audit streams into ONE typed, defensible
answer to "do the completed cycles prove a cost-adjusted edge?":

  - trading_loop_audit.jsonl  -> cycle status distribution (counts)
  - paper_execution_audit.jsonl -> fills + closes (edge, safety, robustness)

What these tests pin (behaviour, not implementation):
  1. COUNTS come from the real loop status enum (completed / cooldown_rejected /
     churn_rejected / entry_mode_blocked / error / ...), not invented.
  2. A forensically-quarantined phantom close (MATIC stale-exit signature) is
     COUNTED as quarantine_rejected and EXCLUDED from every edge figure — it
     never poisons the verdict.
  3. SAFETY is a hard audit assertion: live_orders_attempted == 0 (derived from
     real fill venues, not assumed), entry_mode_blocked count surfaced,
     auto_promotions == 0 (the report decides nothing).
  4. EDGE robustness: result_without_best_trade / result_without_worst_trade
     prove the edge is (or is not) carried by a single outlier. per_symbol is
     correct. trimmed mean and a bootstrap CI are reported.
  5. forward_return fields are honestly marked "pending prospective capture" —
     never fabricated (that capture is an explicit follow-up sprint).

No mocks of the unit under test — real parsing, real CostModel, real quarantine.
"""

from __future__ import annotations

import json

import pytest

from app.execution.cost_model import CostModel
from app.observability.edge_report import ClosedTrade
from app.observability.evidence_window import (
    CANONICAL_EDGE_SOURCES,
    build_evidence_window,
    build_window_from_audit,
    edge_source_of,
    render_window,
)


def _closed(signal_source: str, document_id: str) -> ClosedTrade:
    return ClosedTrade(
        symbol="SLX/USDT",
        position_side="long",
        entry_price=100.0,
        exit_price=128.0,
        quantity=1.0,
        reason="tp",
        trade_pnl_usd=28.0,
        fee_usd=0.2,
        timestamp_utc="2026-06-27T15:00:00+00:00",
        signal_source=signal_source,
        document_id=document_id,
    )


def test_edge_source_of_is_public_and_recovers_mis_bucketed_cohort() -> None:
    # Public contract that ``trading edge-validation`` relies on to keep the
    # anchored verdict's cohort canonical-clean (mirrors the canonical-edge engine).
    momentum = _closed("autonomous_generator", "momentum_universe_SLXUSDT")
    plain = _closed("autonomous_generator", "")
    assert edge_source_of(momentum) == "momentum_universe"  # recovered → NOT canonical
    assert edge_source_of(plain) == "autonomous_generator"  # unchanged → canonical


def test_canonical_filter_excludes_mis_bucketed_cohort_close() -> None:
    # The exact predicate edge-validation applies: a momentum microcap that closed
    # mis-bucketed as autonomous_generator must NOT re-enter the canonical cohort.
    trades = [
        _closed("autonomous_generator", "momentum_universe_SLXUSDT"),  # foreign cohort
        _closed("autonomous_generator", ""),  # genuine autonomous
        _closed("real_analysis", ""),  # genuine canonical
    ]
    kept = [t for t in trades if edge_source_of(t) in CANONICAL_EDGE_SOURCES]
    assert len(kept) == 2
    assert all(edge_source_of(t) in CANONICAL_EDGE_SOURCES for t in kept)
    assert all(t.document_id != "momentum_universe_SLXUSDT" for t in kept)


# --- fixture builders ----------------------------------------------------------


def _loop_cycle(status: str, symbol: str, ts: str) -> dict:
    return {
        "cycle_id": f"cyc_{status}_{symbol}_{ts}",
        "started_at": ts,
        "completed_at": ts,
        "symbol": symbol,
        "status": status,
        "market_data_fetched": status != "entry_mode_blocked",
        "signal_generated": status in {"completed", "risk_rejected"},
        "order_created": status == "completed",
        "fill_simulated": status == "completed",
        "notes": [],
    }


def _entry_fill(symbol: str, ts: str, *, venue: str = "paper") -> dict:
    return {
        "schema_version": "v2",
        "event_type": "order_filled",
        "timestamp_utc": ts,
        "filled_at": ts,
        "symbol": symbol,
        "side": "buy",
        "position_side": "long",
        "fill_price": 100.0,
        "filled_quantity": 1.0,
        "quantity": 1.0,
        "pnl_usd": 0.0,
        "fee_venue": venue,
    }


def _close(symbol: str, entry: float, exit_px: float, ts: str, pnl: float) -> dict:
    return {
        "event_type": "position_closed",
        "symbol": symbol,
        "position_side": "long",
        "entry_price": entry,
        "exit_price": exit_px,
        "quantity": 1.0,
        "reason": "tp" if pnl > 0 else "sl",
        "trade_pnl_usd": pnl,
        "fee_usd": 0.2,
        "timestamp_utc": ts,
    }


def _phantom_matic_close(ts: str) -> dict:
    """A forensically-quarantined corrupt close (DS-20260529-V1 signature)."""
    return {
        "event_type": "position_closed",
        "symbol": "MATIC/USDT",
        "position_side": "long",
        "entry_price": 0.20,
        "exit_price": 0.408545625,  # frozen stale-exit price -> quarantined
        "quantity": 104000.0,
        "reason": "tp",
        "trade_pnl_usd": 73500.0,  # the fake +73.5k profit — must be excluded
        "fee_usd": 0.2,
        "timestamp_utc": ts,
    }


def _write_streams(tmp_path, loop_events, exec_events):
    loop_path = tmp_path / "trading_loop_audit.jsonl"
    exec_path = tmp_path / "paper_execution_audit.jsonl"
    loop_path.write_text(
        "\n".join(json.dumps(e) for e in loop_events) + ("\n" if loop_events else ""),
        encoding="utf-8",
    )
    exec_path.write_text(
        "\n".join(json.dumps(e) for e in exec_events) + ("\n" if exec_events else ""),
        encoding="utf-8",
    )
    return loop_path, exec_path


# --- COUNTS ---------------------------------------------------------------------


def test_counts_map_loop_status_distribution() -> None:
    loop = [
        _loop_cycle("completed", "BTC/USDT", "2026-06-01T10:00:00+00:00"),
        _loop_cycle("completed", "ETH/USDT", "2026-06-01T10:05:00+00:00"),
        _loop_cycle("cooldown_rejected", "BTC/USDT", "2026-06-01T10:10:00+00:00"),
        _loop_cycle("churn_rejected", "BTC/USDT", "2026-06-01T10:15:00+00:00"),
        _loop_cycle("entry_mode_blocked", "BTC/USDT", "2026-06-01T10:20:00+00:00"),
        _loop_cycle("error", "BTC/USDT", "2026-06-01T10:25:00+00:00"),
        _loop_cycle("no_signal", "BTC/USDT", "2026-06-01T10:30:00+00:00"),
    ]
    fills = [_entry_fill("BTC/USDT", "2026-06-01T10:00:01+00:00")]
    report = build_evidence_window(loop_events=loop, exec_events=fills)
    c = report.counts
    assert c.cycles_completed == 2
    assert c.cooldown_rejected == 1
    assert c.churn_rejected == 1
    assert c.errors == 1
    # entry_candidates = cycles that produced a signal worth sizing (not blocked,
    # not no_signal/no_market_data) — here: the 2 completed + the cooldown +
    # churn rejections all had a candidate that was then gated.
    assert c.entry_candidates >= c.cycles_completed
    # paper_entries derived from order_filled buy/entry legs (1 fill above).
    assert c.paper_entries == 1


def test_counts_status_breakdown_is_exhaustive_and_honest() -> None:
    loop = [
        _loop_cycle("completed", "BTC/USDT", "2026-06-01T10:00:00+00:00"),
        _loop_cycle("risk_rejected", "BTC/USDT", "2026-06-01T10:05:00+00:00"),
    ]
    report = build_evidence_window(loop_events=loop, exec_events=[])
    # every status seen is preserved in the raw breakdown (no silent drop).
    assert report.counts.status_breakdown["completed"] == 1
    assert report.counts.status_breakdown["risk_rejected"] == 1
    assert sum(report.counts.status_breakdown.values()) == 2


# --- SAFETY ---------------------------------------------------------------------


def test_safety_live_orders_attempted_is_zero_on_paper_fills() -> None:
    fills = [
        _entry_fill("BTC/USDT", "2026-06-01T10:00:00+00:00", venue="paper"),
        _entry_fill("ETH/USDT", "2026-06-01T10:05:00+00:00", venue="paper"),
    ]
    report = build_evidence_window(loop_events=[], exec_events=fills)
    assert report.safety.live_orders_attempted == 0
    assert report.safety.auto_promotions == 0
    assert report.safety.live_orders_attempted_derivation  # honest derivation note


def test_safety_detects_a_non_paper_fill_as_live_attempt() -> None:
    """If a fill ever carries a non-paper venue, the safety counter MUST catch it.

    This proves the assertion is derived from the data, not hard-coded to 0.
    """
    fills = [
        _entry_fill("BTC/USDT", "2026-06-01T10:00:00+00:00", venue="paper"),
        _entry_fill("ETH/USDT", "2026-06-01T10:05:00+00:00", venue="binance"),
    ]
    report = build_evidence_window(loop_events=[], exec_events=fills)
    assert report.safety.live_orders_attempted == 1


def test_safety_entry_mode_blocked_count_surfaced() -> None:
    loop = [
        _loop_cycle("entry_mode_blocked", "BTC/USDT", "2026-06-01T10:00:00+00:00"),
        _loop_cycle("entry_mode_blocked", "ETH/USDT", "2026-06-01T10:05:00+00:00"),
        _loop_cycle("completed", "BTC/USDT", "2026-06-01T10:10:00+00:00"),
    ]
    report = build_evidence_window(loop_events=loop, exec_events=[])
    assert report.safety.entry_mode_blocked == 2


# --- QUARANTINE -----------------------------------------------------------------


def test_phantom_close_counted_as_quarantine_and_excluded_from_edge() -> None:
    closes = [
        _close("BTC/USDT", 100.0, 102.0, "2026-06-01T10:00:00+00:00", 2.0),
        _close("BTC/USDT", 100.0, 101.0, "2026-06-01T11:00:00+00:00", 1.0),
        _phantom_matic_close("2026-06-01T12:00:00+00:00"),
    ]
    report = build_evidence_window(loop_events=[], exec_events=closes)
    # phantom counted as quarantine_rejected ...
    assert report.counts.quarantine_rejected == 1
    assert report.edge.quarantine_excluded.excluded_count == 1
    # ... and ABSENT from every edge figure (only the 2 BTC closes count).
    assert report.edge.trade_count == 2
    assert "MATIC/USDT" not in {row.cohort_key for row in report.edge.per_symbol_net_bps}
    # the fake +73.5k never leaks into realised pnl
    assert report.edge.realized_pnl_usd_sum < 100.0


# --- EDGE ROBUSTNESS ------------------------------------------------------------


def _ten_closes_one_outlier(tmp_path=None) -> list[dict]:
    """9 small modest winners + 1 huge outlier winner.

    Removing the best trade must drop the mean materially (edge carried by the
    outlier); removing the worst must barely move it.
    """
    out: list[dict] = []
    for i in range(9):
        ts = f"2026-06-01T{10 + i:02d}:00:00+00:00"
        out.append(_close("BTC/USDT", 100.0, 100.5, ts, 0.5))  # +50 bps gross each
    # one outlier: +2000 bps gross
    out.append(_close("BTC/USDT", 100.0, 120.0, "2026-06-01T20:00:00+00:00", 20.0))
    return out


def test_result_without_best_trade_reveals_outlier_dependence() -> None:
    closes = _ten_closes_one_outlier()
    report = build_evidence_window(loop_events=[], exec_events=closes)
    e = report.edge
    assert e.trade_count == 10
    # full mean is inflated by the +2000 bps outlier
    assert e.mean_net_bps > 100.0
    # dropping the best trade collapses the mean toward the modest cohort
    assert e.result_without_best_trade.mean_net_bps < e.mean_net_bps
    assert e.result_without_best_trade.mean_net_bps < 100.0
    # dropping the worst trade barely changes it (worst is just a +50 bps winner)
    assert e.result_without_worst_trade.mean_net_bps > e.result_without_best_trade.mean_net_bps
    # median and trimmed mean resist the single outlier (both modest)
    assert e.median_net_bps < 100.0
    assert e.trimmed_mean_net_bps < e.mean_net_bps


def test_per_symbol_net_bps_is_correct_and_cost_adjusted() -> None:
    closes = [
        _close("BTC/USDT", 100.0, 102.0, "2026-06-01T10:00:00+00:00", 2.0),  # +200 bps gross
        _close("ETH/USDT", 100.0, 100.5, "2026-06-01T11:00:00+00:00", 0.5),  # +50 bps gross
    ]
    report = build_evidence_window(loop_events=[], exec_events=closes)
    by_symbol = {r.cohort_key: r for r in report.edge.per_symbol_net_bps}
    assert set(by_symbol) == {"BTC/USDT", "ETH/USDT"}
    cm = CostModel()
    cost = cm.round_trip(venue="paper").total_cost_bps  # 20 bps on paper default
    # BTC net = 200 - cost ; ETH net = 50 - cost — single-source cost.
    assert by_symbol["BTC/USDT"].net_bps_mean == pytest.approx(200.0 - cost, abs=1e-6)
    assert by_symbol["ETH/USDT"].net_bps_mean == pytest.approx(50.0 - cost, abs=1e-6)


def test_p_mu_above_threshold_and_ci_present_with_enough_samples() -> None:
    # 12 winners with VARIANCE (gross +80..+135 bps) -> P(mu>0) high and a real
    # (non-degenerate) bootstrap CI. Variance matters: identical values give a
    # point CI and float noise around the mean — not a meaningful interval.
    closes = [
        _close(
            "BTC/USDT",
            100.0,
            100.8 + 0.05 * i,  # +80, +85, ... +135 bps gross
            f"2026-06-01T{10 + i:02d}:00:00+00:00",
            0.8 + 0.05 * i,
        )
        for i in range(12)
    ]
    report = build_evidence_window(loop_events=[], exec_events=closes, p_threshold_bps=50.0)
    e = report.edge
    assert e.p_mu_net_positive is not None
    assert e.p_mu_net_positive > 0.9  # net well above 0 for every trade
    assert e.p_mu_net_above_threshold is not None
    assert e.bootstrap_ci_95 is not None
    lo, hi = e.bootstrap_ci_95
    assert lo < hi  # a real interval, not a degenerate point
    assert lo <= e.mean_net_bps <= hi


def test_insufficient_sample_keeps_probabilities_none_not_fabricated() -> None:
    closes = [
        _close("BTC/USDT", 100.0, 101.0, "2026-06-01T10:00:00+00:00", 1.0),
        _close("BTC/USDT", 100.0, 101.0, "2026-06-01T11:00:00+00:00", 1.0),
    ]
    report = build_evidence_window(loop_events=[], exec_events=closes)
    # below MIN_SAMPLE the posterior is honestly None, never invented.
    assert report.edge.p_mu_net_positive is None
    assert report.edge.bootstrap_ci_95 is None


# --- FORWARD-RETURN HONESTY -----------------------------------------------------


def test_forward_returns_marked_pending_not_fabricated() -> None:
    closes = [_close("BTC/USDT", 100.0, 101.0, "2026-06-01T10:00:00+00:00", 1.0)]
    report = build_evidence_window(loop_events=[], exec_events=closes)
    fr = report.edge.forward_return_status
    assert fr["status"] == "pending_prospective_capture"
    # no fabricated numbers
    assert all(v is None for k, v in fr.items() if k.startswith("net_bps_"))


# --- WINDOW METADATA ------------------------------------------------------------


def test_window_metadata_carries_versions_and_bounds() -> None:
    loop = [_loop_cycle("completed", "BTC/USDT", "2026-06-01T10:00:00+00:00")]
    closes = [_close("BTC/USDT", 100.0, 101.0, "2026-06-01T10:00:00+00:00", 1.0)]
    report = build_evidence_window(loop_events=loop, exec_events=closes)
    w = report.window
    assert w.cost_model_version  # non-empty
    assert w.gate_version
    assert w.quarantine_version
    assert w.quarantine_signature_count >= 1


def test_window_bounds_span_both_streams() -> None:
    # last loop cycle at 10:00, but a close at 21:00 — ended_at must cover it.
    loop = [_loop_cycle("completed", "BTC/USDT", "2026-06-01T10:00:00+00:00")]
    closes = [_close("BTC/USDT", 100.0, 101.0, "2026-06-01T21:00:00+00:00", 1.0)]
    report = build_evidence_window(loop_events=loop, exec_events=closes)
    assert report.window.started_at == "2026-06-01T10:00:00+00:00"
    assert report.window.ended_at == "2026-06-01T21:00:00+00:00"


# --- SERIALISATION + RENDER -----------------------------------------------------


def test_report_is_json_serialisable_and_renders() -> None:
    loop = [_loop_cycle("completed", "BTC/USDT", "2026-06-01T10:00:00+00:00")]
    closes = _ten_closes_one_outlier()
    report = build_evidence_window(loop_events=loop, exec_events=closes)
    d = report.to_dict()
    # round-trips cleanly through json
    s = json.dumps(d)
    assert json.loads(s)["edge"]["trade_count"] == 10
    text = render_window(report)
    assert "EVIDENCE WINDOW" in text
    assert "result_without_best" in text or "WITHOUT BEST" in text.upper()
    assert "live_orders_attempted" in text or "LIVE ORDERS ATTEMPTED" in text.upper()


def test_build_from_audit_files_end_to_end(tmp_path) -> None:
    loop = [
        _loop_cycle("completed", "BTC/USDT", "2026-06-01T10:00:00+00:00"),
        _loop_cycle("churn_rejected", "BTC/USDT", "2026-06-01T10:05:00+00:00"),
    ]
    exec_events = [
        _entry_fill("BTC/USDT", "2026-06-01T10:00:01+00:00"),
        _close("BTC/USDT", 100.0, 102.0, "2026-06-01T10:30:00+00:00", 2.0),
        _phantom_matic_close("2026-06-01T11:00:00+00:00"),
    ]
    loop_path, exec_path = _write_streams(tmp_path, loop, exec_events)
    report = build_window_from_audit(loop_audit_path=loop_path, exec_audit_path=exec_path)
    assert report.counts.cycles_completed == 1
    assert report.counts.churn_rejected == 1
    assert report.counts.paper_entries == 1
    assert report.counts.quarantine_rejected == 1
    assert report.edge.trade_count == 1  # only the valid BTC close
    assert report.safety.live_orders_attempted == 0


# --- CANONICAL SOURCE FILTER (2026-06-23 edge-epoch fix) ------------------------
# The full stream mixes the May canary epoch (unattributed closes) into the edge,
# which fabricated a fake positive ETH cohort. The canonical edge restricts the
# EDGE to the real generator's attributed sources; counts + safety stay full.


def _close_src(
    symbol: str, entry: float, exit_px: float, ts: str, pnl: float, source: str | None
) -> dict:
    row = _close(symbol, entry, exit_px, ts, pnl)
    if source is not None:
        row["signal_source"] = source
    return row


def _close_cohort(
    symbol: str,
    entry: float,
    exit_px: float,
    ts: str,
    pnl: float,
    *,
    signal_source: str,
    document_id: str,
) -> dict:
    """A close whose stored signal_source AND originating document_id are both set.

    Models a cohort-feeder close recorded BEFORE the 2026-06-29 forward attribution
    fix: the cohort tag was mis-bucketed into ``signal_source`` (``autonomous_generator``)
    but survives verbatim in ``document_id`` (``<cohort>_<SYM>``).
    """
    row = _close(symbol, entry, exit_px, ts, pnl)
    row["signal_source"] = signal_source
    row["document_id"] = document_id
    return row


def test_canonical_edge_sources_is_the_real_generator() -> None:
    assert CANONICAL_EDGE_SOURCES == frozenset({"autonomous_generator", "real_analysis"})


def test_source_allowlist_restricts_edge_to_canonical_sources() -> None:
    closes = [
        _close_src(
            "BTC/USDT", 100.0, 101.0, "2026-06-12T10:00:00+00:00", 1.0, "autonomous_generator"
        ),
        _close_src("BTC/USDT", 100.0, 102.0, "2026-06-12T11:00:00+00:00", 2.0, "real_analysis"),
        # epoch-foreign, unattributed (May-canary style) — excluded from canonical edge:
        _close_src("ETH/USDT", 100.0, 130.0, "2026-05-20T10:00:00+00:00", 300.0, None),
        # other attributed, non-canonical source (webhook, not a forensically
        # quarantined class) — excluded from the canonical edge purely by source:
        _close_src(
            "SOL/USDT", 100.0, 90.0, "2026-05-21T10:00:00+00:00", -10.0, "tradingview_webhook"
        ),
    ]
    report = build_evidence_window(
        loop_events=[], exec_events=closes, source_allowlist=CANONICAL_EDGE_SOURCES
    )
    assert report.edge.trade_count == 2  # only the two canonical-source closes
    assert report.window.source_allowlist == ("autonomous_generator", "real_analysis")
    assert report.window.closes_excluded_by_source == 2


def test_canonical_edge_excludes_mis_bucketed_cohort_close_via_document_id() -> None:
    # A momentum_universe paper close that resolved BEFORE the 2026-06-29 forward
    # attribution fix carries signal_source="autonomous_generator" (the taxonomy
    # whitelist forgot the cohort) while the cohort tag still survives in
    # document_id ("momentum_universe_<SYM>"). The canonical edge must NOT let that
    # microcap outlier (+2799bps, à la the real SLX close) re-inflate the
    # autonomous edge — same contamination class as the May-canary epoch leak,
    # here via mis-attribution rather than epoch.
    closes = [
        _close_src(
            "BTC/USDT", 100.0, 101.0, "2026-06-12T10:00:00+00:00", 1.0, "autonomous_generator"
        ),
        _close_cohort(
            "SLX/USDT",
            100.0,
            128.0,
            "2026-06-27T15:00:00+00:00",
            28.0,
            signal_source="autonomous_generator",
            document_id="momentum_universe_SLXUSDT",
        ),
    ]
    report = build_evidence_window(
        loop_events=[], exec_events=closes, source_allowlist=CANONICAL_EDGE_SOURCES
    )
    assert report.edge.trade_count == 1  # only the genuine autonomous close
    assert report.window.closes_excluded_by_source == 1
    assert "SLX/USDT" not in {row.cohort_key for row in report.edge.per_symbol_net_bps}


def test_cohort_allowlist_recovers_its_own_mis_bucketed_close_not_blanket_dropped() -> None:
    # The exclusion recovers the TRUE source from document_id — it is NOT a blanket
    # "drop anything tagged momentum_universe". A query that ALLOWS the cohort (the
    # future G3-G7 cohort-edge gate) must KEEP the same close. This locks the
    # invariant that the doc-id override is source-recovery, not source-deletion.
    closes = [
        _close_cohort(
            "SLX/USDT",
            100.0,
            128.0,
            "2026-06-27T15:00:00+00:00",
            28.0,
            signal_source="autonomous_generator",
            document_id="momentum_universe_SLXUSDT",
        ),
    ]
    report = build_evidence_window(
        loop_events=[],
        exec_events=closes,
        source_allowlist=frozenset({"momentum_universe"}),
    )
    assert report.edge.trade_count == 1  # recovered as momentum_universe, in allowlist
    assert report.window.closes_excluded_by_source == 0


def test_no_source_allowlist_is_backward_compatible() -> None:
    closes = [
        _close_src(
            "BTC/USDT", 100.0, 101.0, "2026-06-12T10:00:00+00:00", 1.0, "autonomous_generator"
        ),
        _close_src("ETH/USDT", 100.0, 130.0, "2026-05-20T10:00:00+00:00", 300.0, None),
    ]
    report = build_evidence_window(loop_events=[], exec_events=closes)
    assert report.edge.trade_count == 2  # default unchanged: all closes count
    assert report.window.source_allowlist is None
    assert report.window.closes_excluded_by_source == 0


def test_source_filter_shapes_edge_only_not_counts_or_safety() -> None:
    loop = [
        _loop_cycle("completed", "BTC/USDT", "2026-06-12T10:00:00+00:00"),
        _loop_cycle("completed", "ETH/USDT", "2026-05-20T10:00:00+00:00"),
    ]
    closes = [
        _close_src(
            "BTC/USDT", 100.0, 101.0, "2026-06-12T10:00:00+00:00", 1.0, "autonomous_generator"
        ),
        _close_src("ETH/USDT", 100.0, 130.0, "2026-05-20T10:00:00+00:00", 300.0, None),
    ]
    report = build_evidence_window(
        loop_events=loop, exec_events=closes, source_allowlist=CANONICAL_EDGE_SOURCES
    )
    assert report.edge.trade_count == 1  # only the canonical close in the edge
    assert report.counts.cycles_completed == 2  # BOTH cycles still counted
    assert report.safety.live_orders_attempted == 0


def test_canonical_filter_threads_through_build_from_audit(tmp_path) -> None:
    exec_events = [
        _close_src(
            "BTC/USDT", 100.0, 101.0, "2026-06-12T10:00:00+00:00", 1.0, "autonomous_generator"
        ),
        _close_src("ETH/USDT", 100.0, 130.0, "2026-05-20T10:00:00+00:00", 300.0, None),
    ]
    loop_path, exec_path = _write_streams(tmp_path, [], exec_events)
    report = build_window_from_audit(
        loop_audit_path=loop_path,
        exec_audit_path=exec_path,
        source_allowlist=CANONICAL_EDGE_SOURCES,
    )
    assert report.edge.trade_count == 1
    assert report.window.closes_excluded_by_source == 1


def test_render_makes_the_source_filter_status_visible() -> None:
    # Honesty: a contaminated (unfiltered) read must announce itself; the
    # canonical read must show which sources shaped the edge.
    closes = [
        _close_src(
            "BTC/USDT", 100.0, 101.0, "2026-06-12T10:00:00+00:00", 1.0, "autonomous_generator"
        )
    ]
    full = render_window(build_evidence_window(loop_events=[], exec_events=closes))
    canon = render_window(
        build_evidence_window(
            loop_events=[], exec_events=closes, source_allowlist=CANONICAL_EDGE_SOURCES
        )
    )
    assert "FULL STREAM" in full
    assert "CANONICAL" in canon and "autonomous_generator" in canon
