"""Unit tests for the L2 on-chain evidence provider wiring (Sprint 2).

Default-off contract + fail-safe gates (missing/stale stream, too little history),
and the B-003 invariant: when armed, the provider WRITES raw features to the
shadow log and returns an INERT evidence (direction_aligned=0) — measurement only,
zero sizing impact until evaluation learns a direction.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.core.evidence_settings import L2OnChainEvidenceSettings
from app.signals.l2_wiring import build_l2_onchain_evidence_provider
from app.signals.models import SignalDirection


def _md(symbol: str = "BTC/USDT") -> SimpleNamespace:
    return SimpleNamespace(symbol=symbol)


def _write_stream(path, *, n: int, current_ts: str, fee: float = 5.0, mempool: int = 3000) -> None:
    with path.open("w", encoding="utf-8") as fh:
        # history: ascending fees so the current value lands at a known percentile
        for i in range(n):
            fh.write(
                json.dumps(
                    {
                        "ts": f"2026-06-01T00:{i:02d}:00+00:00",
                        "fee_sat_vb": float(i + 1),
                        "mempool_tx": (i + 1) * 100,
                    }
                )
                + "\n"
            )
        # current (latest) record drives the "now" fee/mempool
        fh.write(json.dumps({"ts": current_ts, "fee_sat_vb": fee, "mempool_tx": mempool}) + "\n")


def test_disabled_returns_none() -> None:
    assert build_l2_onchain_evidence_provider(L2OnChainEvidenceSettings(enabled=False)) is None


def test_default_settings_is_disabled() -> None:
    assert build_l2_onchain_evidence_provider(L2OnChainEvidenceSettings()) is None


def test_missing_stream_returns_empty(tmp_path) -> None:
    cfg = L2OnChainEvidenceSettings(enabled=True, stream_path=tmp_path / "nope.jsonl")
    provider = build_l2_onchain_evidence_provider(cfg)
    assert provider is not None
    assert provider(None, _md(), SignalDirection.LONG) == ()


def test_below_min_window_returns_empty(tmp_path) -> None:
    stream = tmp_path / "s.jsonl"
    _write_stream(stream, n=3, current_ts=datetime.now(UTC).isoformat())  # only 3 history < min
    cfg = L2OnChainEvidenceSettings(enabled=True, stream_path=stream, min_window=20)
    provider = build_l2_onchain_evidence_provider(cfg)
    assert provider(None, _md(), SignalDirection.LONG) == ()


def test_stale_stream_returns_empty(tmp_path) -> None:
    stream = tmp_path / "s.jsonl"
    old = (datetime.now(UTC) - timedelta(hours=5)).isoformat()
    _write_stream(stream, n=30, current_ts=old)
    cfg = L2OnChainEvidenceSettings(
        enabled=True, stream_path=stream, ttl_seconds=3600, min_window=5
    )
    provider = build_l2_onchain_evidence_provider(cfg)
    assert provider(None, _md(), SignalDirection.LONG) == ()


def test_armed_writes_shadow_and_returns_inert_evidence(tmp_path) -> None:
    stream = tmp_path / "s.jsonl"
    shadow = tmp_path / "l2_shadow.jsonl"
    # current fee=100 is above all history (1..30) → fee_percentile high/extreme
    _write_stream(stream, n=30, current_ts=datetime.now(UTC).isoformat(), fee=100.0, mempool=99999)
    cfg = L2OnChainEvidenceSettings(
        enabled=True, stream_path=stream, shadow_log_path=shadow, min_window=5, source_trust=0.5
    )
    provider = build_l2_onchain_evidence_provider(cfg)

    out = provider(None, _md("ETH/USDT"), SignalDirection.SHORT)
    assert len(out) == 1
    ev = out[0]
    assert ev.direction_aligned == 0  # B-003: inert until direction is learned
    assert ev.value > 0.0  # extreme on-chain state → nonzero magnitude
    assert ev.source_trust == 0.5

    line = json.loads(shadow.read_text(encoding="utf-8").strip())
    assert line["symbol"] == "ETH/USDT"
    assert line["direction"] == "short"
    assert line["fee_percentile"] == 1.0  # current fee above all history
    assert "evidence_direction_aligned" not in line  # raw features only (B-003)


def test_composite_includes_l2_when_armed(tmp_path) -> None:
    from app.core.evidence_settings import (
        FundingEvidenceSettings,
        OpenInterestEvidenceSettings,
    )
    from app.signals.bayesian_confidence import EvidenceKind
    from app.signals.composite_evidence_wiring import build_composite_evidence_provider

    stream = tmp_path / "s.jsonl"
    _write_stream(stream, n=30, current_ts=datetime.now(UTC).isoformat(), fee=100.0)
    l2 = L2OnChainEvidenceSettings(
        enabled=True, stream_path=stream, shadow_log_path=tmp_path / "sh.jsonl", min_window=5
    )
    provider = build_composite_evidence_provider(
        FundingEvidenceSettings(), OpenInterestEvidenceSettings(), l2_settings=l2
    )
    assert provider is not None
    out = provider(None, _md(), SignalDirection.LONG)
    assert any(e.kind == EvidenceKind.L2_ONCHAIN for e in out)


# ── Zeitmodell (Befund 3): L1 „Stand Schnitt“ ────────────────────────────────


def _bound(cutoff: datetime):  # noqa: ANN202
    from app.core.l2_candidate_context import bind_candidate

    return bind_candidate(
        candidate_id="cyc-t",
        cycle_started_at=(cutoff - timedelta(seconds=0.4)).isoformat(),
        input_cutoff_ts=cutoff.isoformat(),
        reference_price_ts=(cutoff - timedelta(seconds=0.2)).isoformat(),
    )


def _armed(tmp_path):  # noqa: ANN202
    stream = tmp_path / "s.jsonl"
    shadow = tmp_path / "l2_shadow.jsonl"
    cfg = L2OnChainEvidenceSettings(
        enabled=True, stream_path=stream, shadow_log_path=shadow, min_window=5, ttl_seconds=3600
    )
    return stream, shadow, build_l2_onchain_evidence_provider(cfg)


def _append(stream, ts: datetime, fee: float) -> None:
    with stream.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": ts.isoformat(), "fee_sat_vb": fee, "mempool_tx": 1}) + "\n")


def test_l1_record_that_arrived_after_the_cutoff_is_ignored(tmp_path) -> None:
    cutoff = datetime.now(UTC) - timedelta(minutes=1)
    stream, shadow, provider = _armed(tmp_path)
    as_of = cutoff - timedelta(minutes=10)
    _write_stream(stream, n=30, current_ts=as_of.isoformat(), fee=100.0)
    _append(stream, cutoff + timedelta(seconds=30), fee=0.5)  # spaet eingetroffen
    with _bound(cutoff):
        assert len(provider(None, _md(), SignalDirection.LONG)) == 1
    line = json.loads(shadow.read_text(encoding="utf-8").strip())
    assert line["fee_sat_vb"] == 100.0  # der Satz „Stand Schnitt“, nicht der spaete
    assert line["l1_observed_ts"] == as_of.isoformat()
    assert line["causality_ok"] is True
    assert line["candidate_id"] == "cyc-t"


def test_future_dated_l1_record_is_ignored(tmp_path) -> None:
    stream, shadow, provider = _armed(tmp_path)
    now = datetime.now(UTC)
    _write_stream(stream, n=30, current_ts=now.isoformat(), fee=100.0)
    _append(stream, now + timedelta(hours=1), fee=0.5)  # Uhrenversatz
    assert len(provider(None, _md(), SignalDirection.LONG)) == 1
    line = json.loads(shadow.read_text(encoding="utf-8").strip())
    assert line["fee_sat_vb"] == 100.0
    assert line["l1_observed_ts"] == now.isoformat()


def test_staleness_is_measured_at_the_cutoff(tmp_path) -> None:
    cutoff = datetime.now(UTC) - timedelta(hours=2)
    stream, shadow, provider = _armed(tmp_path)
    _write_stream(stream, n=30, current_ts=(cutoff - timedelta(minutes=10)).isoformat())
    with _bound(cutoff):
        # 10 min vor dem Schnitt ist frisch (ttl 1 h), obwohl es jetzt 2 h alt ist.
        assert len(provider(None, _md(), SignalDirection.LONG)) == 1
