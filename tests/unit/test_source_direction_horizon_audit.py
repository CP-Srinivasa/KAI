"""Unit tests for the Track 2.3 read-only edge audit."""

from __future__ import annotations

from app.observability.source_direction_horizon_audit import (
    asset_bucket,
    build_audit,
    load_resolved,
    normalize_side,
    normalize_source,
)


def test_normalizers_and_buckets() -> None:
    assert normalize_source("Decrypt") == "decrypt"
    assert normalize_source("CoinTelegraph") == "cointelegraph"
    assert normalize_side("bullish") == "long" and normalize_side("SELL") == "short"
    assert asset_bucket("BTC/USDT") == "major"
    assert asset_bucket("SOL/USDT") == "alt"
    assert asset_bucket("USDT/USD") == "stable"


def _row(source, side, symbol, fwds, score=0.5, canary=False):
    r = {
        "source": source,
        "side": side,
        "symbol": symbol,
        "signal_confidence": score,
        "is_canary": canary,
    }
    for k, v in fwds.items():
        r[k] = v
    return r


def test_side_adjustment_and_invert_counterfactual() -> None:
    # 40 short rows that all FELL 30bps → a profitable short (adj +30); inverting
    # would lose. EVnet = 30 - 20 = 10; inverted = -30 - 20 = -50.
    rows = [
        _row("technical_screener", "short", "ETH/USDT", {"fwd_3600s_bps": -30.0}) for _ in range(40)
    ]
    cohorts = build_audit(rows)
    c = next(c for c in cohorts if c.direction == "short")
    h = c.horizons["1h"]
    assert h.hit_rate == 100.0  # short + price fell = hit
    assert h.mean_bps == 30.0 and h.ev_net_bps == 10.0
    assert h.inverted_ev_bps == -50.0  # inverting this winning short would lose


def test_insufficient_below_min_n() -> None:
    rows = [_row("x", "long", "BTC/USDT", {"fwd_60s_bps": 5.0}) for _ in range(5)]
    assert build_audit(rows)[0].verdict == "INSUFFICIENT"


def test_contrarian_candidate_when_original_loses_inverted_wins() -> None:
    # 40 short rows that ROSE 40bps → losing short (adj -40, EVnet -60); inverted
    # = +40 - 20 = +20 > 0 → CONTRARIAN_CANDIDATE.
    rows = [
        _row("s", "short", "ALT/USDT", {"fwd_3600s_bps": 40.0, "fwd_60s_bps": 40.0})
        for _ in range(40)
    ]
    c = next(c for c in build_audit(rows) if c.direction == "short")
    assert c.horizons["1h"].inverted_ev_bps == 20.0
    assert c.verdict == "CONTRARIAN_CANDIDATE"


def test_carrier_long_when_positive_multi_horizon() -> None:
    rows = [
        _row(
            "technical_screener", "long", "BTC/USDT", {"fwd_900s_bps": 35.0, "fwd_3600s_bps": 40.0}
        )
        for _ in range(40)
    ]
    c = next(c for c in build_audit(rows) if c.direction == "long")
    assert c.verdict == "CARRIER_LONG"


def test_outlier_inflated_mean_is_not_a_carrier() -> None:
    # 36 flat (0 bps) + 4 huge winners (+1000): mean EV is positive but the
    # trimmed/robust EV is ~0 → must NOT mint a CARRIER_LONG (the honesty fix).
    flat = [
        _row("technical_screener", "long", "BTC/USDT", {"fwd_3600s_bps": 0.0}) for _ in range(36)
    ]
    spikes = [
        _row("technical_screener", "long", "BTC/USDT", {"fwd_3600s_bps": 1000.0}) for _ in range(4)
    ]
    c = next(c for c in build_audit(flat + spikes) if c.direction == "long")
    h = c.horizons["1h"]
    assert h.ev_net_bps is not None and h.ev_net_bps > 0  # mean-based looks great
    assert h.robust_ev_bps is not None and h.robust_ev_bps <= 0  # trimmed is honest
    assert c.verdict != "CARRIER_LONG"


def test_canary_filtered_and_missing_file(tmp_path) -> None:
    rows, skipped = load_resolved(tmp_path / "nope.jsonl")
    assert rows == [] and skipped == 0
