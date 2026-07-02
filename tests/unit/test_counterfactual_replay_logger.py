"""Tests für den Counterfactual Live∥Replay-Drift-Logger (#318 Phase 1, pur)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from app.observability.counterfactual_replay_logger import (
    bar_covering,
    build_comparison,
    run_counterfactual_pass,
)

_ENTRY = datetime(2026, 6, 1, 0, 0, 30, tzinfo=UTC)
_ENTRY_MS = int(_ENTRY.timestamp() * 1000)
_BAR_OPEN = int(datetime(2026, 6, 1, 0, 0, 0, tzinfo=UTC).timestamp() * 1000)
# (open_ms, high, low, close)
_BAR = (_BAR_OPEN, 110.0, 90.0, 100.0)
_NOW = datetime(2026, 6, 1, 1, 0, 0, tzinfo=UTC)  # weit nach der Entry-Minute


def _cand(entry_price: float, **over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "candidate_id": "c1",
        "ts_utc": _ENTRY.isoformat(),
        "symbol": "BTC/USDT",
        "side": "long",
        "entry_price": entry_price,
        "candidate_kind": "signal_candidate",
        "source": "autonomous_generator",
    }
    base.update(over)
    return base


def test_bar_covering_finds_minute_and_returns_none_outside() -> None:
    assert bar_covering(_ENTRY_MS, [_BAR]) == _BAR
    # Entry vor allen Bars → None
    assert bar_covering(_BAR_OPEN - 120_000, [_BAR]) is None
    # Entry nach dem Bar-Minutenfenster → None
    assert bar_covering(_BAR_OPEN + 120_000, [_BAR]) is None


def test_build_comparison_in_range_not_exceeded() -> None:
    rec = build_comparison(_cand(100.0), [_BAR], threshold_bps=30.0)
    assert rec is not None
    assert rec["in_settled_range"] is True
    assert rec["drift_to_range_bps"] == 0.0
    assert rec["drift_to_close_bps"] == 0.0
    assert rec["drift_exceeded"] is False


def test_build_comparison_out_of_range_exceeded() -> None:
    rec = build_comparison(_cand(120.0), [_BAR], threshold_bps=30.0)
    assert rec is not None
    assert rec["in_settled_range"] is False
    # 120 vs high 110 → (10/110)*1e4 ≈ 909 bps
    assert rec["drift_to_range_bps"] == round((120.0 - 110.0) / 110.0 * 1e4, 2)
    assert rec["drift_exceeded"] is True


def test_build_comparison_just_out_of_range_under_threshold() -> None:
    # 110.2 liegt knapp über high (110) → out of range, aber < 30 bps Drift
    rec = build_comparison(_cand(110.2), [_BAR], threshold_bps=30.0)
    assert rec is not None
    assert rec["in_settled_range"] is False
    assert abs(float(rec["drift_to_range_bps"])) < 30.0
    assert rec["drift_exceeded"] is False


def test_build_comparison_none_when_no_covering_bar_or_no_entry() -> None:
    far = (_BAR_OPEN + 600_000, 1.0, 1.0, 1.0)
    assert build_comparison(_cand(100.0), [far]) is None  # keine deckende Kline
    assert build_comparison(_cand(0.0), [_BAR]) is None  # kein gültiger Entry


def test_build_comparison_implausible_price_flagged_suspect_not_drift() -> None:
    # entry_live 100000 vs Kline-Range [90,110]: >30% ausserhalb = Feed-/Einheiten-
    # Glitch (z. B. ENA entry_live~100 vs echte 0.094), KEIN echter Markt-Drift.
    rec = build_comparison(_cand(100_000.0), [_BAR], threshold_bps=30.0)
    assert rec is not None
    assert rec["data_quality_suspect"] is True
    # Glitch darf NICHT als drift_exceeded zählen (verzerrt sonst die Statistik).
    assert rec["drift_exceeded"] is False
    assert rec["schema_version"] == "v2"


def test_build_comparison_normal_out_of_range_not_suspect() -> None:
    # 120 vs high 110 = ~909 bps: echter (kleiner) Out-of-Range-Drift, KEIN Glitch.
    rec = build_comparison(_cand(120.0), [_BAR], threshold_bps=30.0)
    assert rec is not None
    assert rec["data_quality_suspect"] is False
    assert rec["drift_exceeded"] is True


def test_build_comparison_passes_through_gate_would_reject() -> None:
    rej = build_comparison(_cand(100.0, gate_would_reject=True), [_BAR])
    assert rej is not None
    assert rej["gate_would_reject"] is True
    # Kandidat ohne das Feld (z. B. autonomous_generator) → None (nicht erfunden).
    plain = build_comparison(_cand(100.0), [_BAR])
    assert plain is not None
    assert plain["gate_would_reject"] is None


def test_run_pass_counts_suspect_separately(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    out = tmp_path / "cf.jsonl"
    ledger.write_text(
        "\n".join(
            [
                json.dumps(_cand(120.0, candidate_id="real")),  # echter Drift
                json.dumps(_cand(100_000.0, candidate_id="glitch")),  # Glitch → suspect
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    def _fetch(_sym: str, _start: int, _end: int):
        return [_BAR]

    counts = run_counterfactual_pass(
        fetch_klines=_fetch, now=_NOW, ledger_path=ledger, output_path=out, threshold_bps=30.0
    )
    assert counts["compared"] == 2
    assert counts["exceeded"] == 1  # nur der echte Drift, NICHT der Glitch
    assert counts["suspect"] == 1
    # Beide Records werden geschrieben (nicht-destruktiv, audit-transparent).
    written = [json.loads(ln) for ln in out.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(written) == 2


def test_run_pass_idempotent_and_counts(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    out = tmp_path / "cf.jsonl"
    ledger.write_text(
        "\n".join(
            [
                json.dumps(_cand(120.0, candidate_id="a")),  # out-of-range → exceeded
                json.dumps(_cand(100.0, candidate_id="b")),  # in range
                json.dumps(_cand(100.0, candidate_id="canary", source="canary_probe")),  # skip
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    def _fetch(_sym: str, _start: int, _end: int):
        return [_BAR]

    counts = run_counterfactual_pass(
        fetch_klines=_fetch, now=_NOW, ledger_path=ledger, output_path=out, threshold_bps=30.0
    )
    assert counts["compared"] == 2
    assert counts["exceeded"] == 1
    assert counts["skipped_kind"] == 1
    # Zweiter Lauf: alles schon verglichen → idempotent
    counts2 = run_counterfactual_pass(
        fetch_klines=_fetch, now=_NOW, ledger_path=ledger, output_path=out, threshold_bps=30.0
    )
    assert counts2["compared"] == 0
    assert counts2["already"] == 2


def test_run_pass_skips_too_recent(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    out = tmp_path / "cf.jsonl"
    ledger.write_text(json.dumps(_cand(100.0)) + "\n", encoding="utf-8")
    # now nur 10s nach Entry → Kline nicht gesettelt → skipped_recent
    soon = datetime(2026, 6, 1, 0, 0, 40, tzinfo=UTC)

    def _fetch(_sym: str, _start: int, _end: int):
        return [_BAR]

    counts = run_counterfactual_pass(
        fetch_klines=_fetch, now=soon, ledger_path=ledger, output_path=out
    )
    assert counts["compared"] == 0
    assert counts["skipped_recent"] == 1
