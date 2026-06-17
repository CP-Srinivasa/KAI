"""Phase-B Shadow-Candidate-Ledger: pure-compute + offline resolve tests.

Synthetic bars only — no network. Pins the diagnose primitives (forward returns,
MAE/MFE, mfe_before_mae ordering, reached_take/stop) and the idempotent resolver.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.observability import shadow_candidate_ledger as scl
from app.observability.shadow_candidate_ledger import (
    ShadowCandidate,
    compute_excursion,
    compute_forward_returns,
    record_candidate,
    resolve_pending,
    side_adjusted_bps,
)

T0 = datetime(2026, 6, 2, 12, 0, 0, tzinfo=UTC)
T0_MS = int(T0.timestamp() * 1000)


def _bar(offset_s: int, high: float, low: float, close: float) -> scl.Bar:
    return (T0_MS + offset_s * 1000, high, low, close)


# --- side_adjusted_bps ------------------------------------------------------ #


def test_side_adjusted_bps_long_and_short() -> None:
    # +1% move
    assert side_adjusted_bps(100.0, 101.0, "long") == 100.0
    assert side_adjusted_bps(100.0, 101.0, "short") == -100.0
    assert side_adjusted_bps(100.0, 99.0, "short") == 100.0
    assert side_adjusted_bps(0.0, 101.0, "long") == 0.0


# --- forward returns -------------------------------------------------------- #


def test_forward_returns_pick_last_bar_at_or_before_horizon() -> None:
    bars = [
        _bar(0, 100, 100, 100.0),
        _bar(60, 101, 100, 101.0),  # +100 bps at 60s
        _bar(300, 102, 101, 102.0),  # +200 bps at 300s
    ]
    out = compute_forward_returns(entry_price=100.0, side="long", entry_ts_ms=T0_MS, bars=bars)
    assert out["fwd_60s_bps"] == 100.0
    assert out["fwd_300s_bps"] == 200.0
    # 900s / 3600s not covered → None (never silently 0)
    assert out["fwd_900s_bps"] is None
    assert out["fwd_3600s_bps"] is None


def test_forward_returns_short_inverts_sign() -> None:
    bars = [_bar(0, 100, 100, 100.0), _bar(60, 100, 99, 99.0)]
    out = compute_forward_returns(entry_price=100.0, side="short", entry_ts_ms=T0_MS, bars=bars)
    assert out["fwd_60s_bps"] == 100.0  # price fell 1% → favourable for short


# --- MAE / MFE -------------------------------------------------------------- #


def test_excursion_long_mfe_before_mae() -> None:
    # goes up first (+150bps high at 60s) then down (-200bps low at 300s)
    bars = [
        _bar(60, 101.5, 100.0, 101.0),
        _bar(300, 100.5, 98.0, 98.5),
    ]
    r = compute_excursion(entry_price=100.0, side="long", entry_ts_ms=T0_MS, bars=bars)
    assert r.mfe_bps == 150.0
    assert r.mae_bps == -200.0
    assert r.mfe_before_mae is True
    assert r.time_to_mfe_s == 60.0
    assert r.time_to_mae_s == 300.0
    assert r.bars_seen == 2


def test_excursion_adverse_selection_pattern() -> None:
    # straight down: MAE early, MFE ~0 → adverse selection signature
    bars = [_bar(60, 100.0, 98.0, 98.5), _bar(300, 99.0, 97.0, 97.5)]
    r = compute_excursion(entry_price=100.0, side="long", entry_ts_ms=T0_MS, bars=bars)
    assert r.mfe_bps == 0.0  # never went above entry
    assert r.mae_bps == -300.0
    assert r.mfe_before_mae is True  # both at first bar tie → fav_ms<=adv_ms


def test_excursion_ignores_bars_outside_window() -> None:
    bars = [_bar(60, 105.0, 100.0, 104.0), _bar(99999, 200.0, 100.0, 200.0)]
    r = compute_excursion(
        entry_price=100.0, side="long", entry_ts_ms=T0_MS, bars=bars, window_s=3600
    )
    assert r.bars_seen == 1
    assert r.mfe_bps == 500.0


# --- candidate record ------------------------------------------------------- #


def test_from_geometry_computes_distances_and_rr() -> None:
    c = ShadowCandidate.from_geometry(
        candidate_id="c1",
        ts_utc=T0.isoformat(),
        symbol="BTC/USDT",
        side="long",
        entry_price=100.0,
        stop_price=99.0,  # 100 bps
        take_price=102.0,  # 200 bps
    )
    assert c.stop_dist_bps == 100.0
    assert c.take_dist_bps == 200.0
    assert c.rr == 2.0


def test_record_candidate_appends(tmp_path: Path) -> None:
    p = tmp_path / "ledger.jsonl"
    c = ShadowCandidate(
        candidate_id="c1", ts_utc=T0.isoformat(), symbol="BTC/USDT", side="long", entry_price=100.0
    )
    assert record_candidate(c, path=p) is True
    assert record_candidate(c, path=p) is True
    assert len(p.read_text(encoding="utf-8").splitlines()) == 2


# --- resolver --------------------------------------------------------------- #


def _write_candidate(path: Path, **over: object) -> None:
    c = ShadowCandidate.from_geometry(
        candidate_id=str(over.get("candidate_id", "c1")),
        ts_utc=str(over.get("ts_utc", T0.isoformat())),
        symbol=str(over.get("symbol", "BTC/USDT")),
        side=str(over.get("side", "long")),
        entry_price=float(over.get("entry_price", 100.0)),  # type: ignore[arg-type]
        stop_price=over.get("stop_price", 99.0),  # type: ignore[arg-type]
        take_price=over.get("take_price", 102.0),  # type: ignore[arg-type]
    )
    record_candidate(c, path=path)


def test_resolve_pending_skips_recent_and_is_idempotent(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    resolved = tmp_path / "resolved.jsonl"
    _write_candidate(ledger)

    # bars: up to +150bps MFE, down to -120bps MAE; close at +80bps by 3600s
    bars = [
        _bar(60, 101.5, 100.0, 101.2),
        _bar(900, 101.0, 98.8, 100.5),
        _bar(3600, 101.0, 99.0, 100.8),
    ]

    def fetch(symbol: str, start_ms: int, end_ms: int) -> list[scl.Bar]:
        return bars

    # now only 10 min after T0 → window (3600s) NOT elapsed → skipped_recent
    early = resolve_pending(
        fetch_klines=fetch,
        now=T0 + timedelta(minutes=10),
        ledger_path=ledger,
        resolved_path=resolved,
    )
    assert early["resolved"] == 0
    assert early["skipped_recent"] == 1
    assert not resolved.exists()

    # now 2h after T0 → window elapsed → resolves
    late = resolve_pending(
        fetch_klines=fetch, now=T0 + timedelta(hours=2), ledger_path=ledger, resolved_path=resolved
    )
    assert late["resolved"] == 1
    rec = resolved.read_text(encoding="utf-8").splitlines()
    assert len(rec) == 1

    # idempotent: second run resolves nothing new
    again = resolve_pending(
        fetch_klines=fetch, now=T0 + timedelta(hours=2), ledger_path=ledger, resolved_path=resolved
    )
    assert again["resolved"] == 0
    assert again["already"] == 1


def test_resolve_pending_reached_take_and_stop_flags(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    resolved = tmp_path / "resolved.jsonl"
    # stop 100bps, take 200bps
    _write_candidate(ledger, stop_price=99.0, take_price=102.0)

    import json

    def fetch(symbol: str, start_ms: int, end_ms: int) -> list[scl.Bar]:
        # MFE +250bps (>take 200), MAE -50bps (>-stop 100 → stop NOT reached)
        return [_bar(60, 102.5, 100.0, 102.0), _bar(3600, 102.0, 99.5, 101.0)]

    resolve_pending(
        fetch_klines=fetch, now=T0 + timedelta(hours=2), ledger_path=ledger, resolved_path=resolved
    )
    r = json.loads(resolved.read_text(encoding="utf-8").splitlines()[0])
    assert r["reached_take"] is True
    assert r["reached_stop"] is False
    assert r["mfe_bps"] == 250.0


# --- WP-A: technical-screener candidates must be resolvable ----------------- #


def test_is_resolvable_includes_technical_but_headline_excludes_it() -> None:
    # WP-A 2026-06-16: the technical screener writes candidate_kind="technical".
    # It must be resolvable (forward returns) yet stay OUT of the autonomous-
    # generator headline (B-002): resolved for measurement, never a REAL source.
    assert "technical" in scl.RESOLVABLE_CANDIDATE_KINDS
    tech_row = {"candidate_kind": "technical", "source": "technical_screener"}
    assert scl._is_resolvable_candidate(tech_row, include_canary=False) is True
    # headline isolation: technical_screener is not a REAL (headline) source
    assert "technical_screener" not in scl.REAL_SOURCES


def test_resolve_pending_resolves_technical_screener_candidate(tmp_path: Path) -> None:
    # Regression: before WP-A, candidate_kind="technical" was silently skipped
    # (skipped_kind) so the technical path produced ZERO edge evidence.
    ledger = tmp_path / "ledger.jsonl"
    resolved = tmp_path / "resolved.jsonl"
    c = ShadowCandidate.from_geometry(
        candidate_id="tech-1",
        ts_utc=T0.isoformat(),
        symbol="TRX/USDT",
        side="long",
        entry_price=100.0,
        stop_price=None,
        take_price=None,
        source="technical_screener",
        candidate_kind="technical",
    )
    record_candidate(c, path=ledger)

    bars = [_bar(60, 101.0, 100.0, 100.5), _bar(3600, 101.0, 99.5, 100.8)]

    def fetch(symbol: str, start_ms: int, end_ms: int) -> list[scl.Bar]:
        return bars

    counts = resolve_pending(
        fetch_klines=fetch, now=T0 + timedelta(hours=2), ledger_path=ledger, resolved_path=resolved
    )
    assert counts["resolved"] == 1
    assert counts["skipped_kind"] == 0
    import json

    rec = json.loads(resolved.read_text(encoding="utf-8").splitlines()[0])
    assert rec["candidate_kind"] == "technical"
    assert rec["source"] == "technical_screener"
