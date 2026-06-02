"""Phase-B Binance resolver shell — pair mapping, fail-soft fetch, wiring.

No real network: urlopen is patched. Pins that a failed fetch returns None
(candidate stays pending) and that resolve_with_binance wires the fetcher into
the pure resolver.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.observability import shadow_resolver as sr
from app.observability.shadow_candidate_ledger import ShadowCandidate, record_candidate

T0 = datetime(2026, 6, 2, 12, 0, 0, tzinfo=UTC)


def test_pair_mapping() -> None:
    assert sr._to_binance_pair("BTC/USDT") == "BTCUSDT"
    assert sr._to_binance_pair("eth-usdt") == "ETHUSDT"


def test_kline_fetch_failsoft_returns_none(monkeypatch) -> None:
    def boom(*a, **k):
        raise OSError("network down")

    monkeypatch.setattr(sr.urllib.request, "urlopen", boom)
    assert sr.binance_kline_fetcher("BTC/USDT", 0, 1000) is None


def test_resolve_with_binance_wires_fetcher(tmp_path: Path, monkeypatch) -> None:
    ledger = tmp_path / "ledger.jsonl"
    resolved = tmp_path / "resolved.jsonl"
    cand = ShadowCandidate.from_geometry(
        candidate_id="c1",
        ts_utc=T0.isoformat(),
        symbol="BTC/USDT",
        side="long",
        entry_price=100.0,
        stop_price=99.0,
        take_price=102.0,
    )
    record_candidate(cand, path=ledger)

    t0_ms = int(T0.timestamp() * 1000)
    synthetic = [
        (t0_ms + 60_000, 101.5, 100.0, 101.0),
        (t0_ms + 3600_000, 101.0, 99.5, 100.5),
    ]
    monkeypatch.setattr(sr, "binance_kline_fetcher", lambda *a, **k: synthetic)

    counts = sr.resolve_with_binance(
        now=T0 + timedelta(hours=2), ledger_path=ledger, resolved_path=resolved
    )
    assert counts["resolved"] == 1
    rec = json.loads(resolved.read_text(encoding="utf-8").splitlines()[0])
    assert rec["candidate_id"] == "c1"
    assert rec["mfe_bps"] == 150.0
    assert rec["stop_dist_bps"] == 100.0
