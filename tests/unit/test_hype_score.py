"""Hype-Score-Core (HYPE-S1) — Scoring- und Aggregations-Vertrag.

Risiko, das diese Tests abdecken: ein falscher Score dämpft entweder gute
Signale grundlos (false positive) oder lässt Hype-Einstiege ungebremst durch
(false negative). Geprüft werden deshalb die Vertrags-Eckpunkte: Floor,
Baseline-Normalität, Sättigung, Amplifikations-Cap, kalte Baseline und das
deterministische Bucketing der Aggregation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.risk.hype_score import (
    AMPLIFIER_BASE,
    DocMention,
    HypeInputs,
    HypeScoreConfig,
    aggregate_hype_inputs,
    compute_hype_score,
)

_CFG = HypeScoreConfig()  # Defaults: 6h Fenster, 7d Baseline, min 5, sat 5×/5 Quellen
_NOW = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)


def _inputs(**overrides: object) -> HypeInputs:
    base: dict[str, object] = {
        "asset": "BTC",
        "mentions_recent": 20,
        "mentions_baseline_total": 140,  # 140 / (7d/6h=28 Fenster) = 5.0 pro Fenster
        "distinct_sources_recent": 5,
        "bullish_recent": 18,
        "bearish_recent": 2,
    }
    base.update(overrides)
    return HypeInputs(**base)  # type: ignore[arg-type]


# ── Floor + Normalität ────────────────────────────────────────────────────────


def test_below_min_mentions_yields_zero_with_insufficient_data_flag() -> None:
    result = compute_hype_score(_inputs(mentions_recent=4), _CFG)
    assert result.score == 0.0
    assert result.insufficient_data is True
    assert "fail-safe" in result.rationale


def test_normal_newsflow_at_baseline_scores_zero() -> None:
    # 5 Mentions bei Baseline 5/Fenster = 1.0× → keine abnormale Velocity.
    result = compute_hype_score(_inputs(mentions_recent=5), _CFG)
    assert result.velocity_component == 0.0
    assert result.score == 0.0
    assert result.insufficient_data is False


# ── Velocity-Sättigung + Amplifikation ────────────────────────────────────────


def test_velocity_saturates_at_configured_multiple_of_baseline() -> None:
    # 25 Mentions = 5× Baseline = velocity_saturation → Komponente 1.0.
    result = compute_hype_score(_inputs(mentions_recent=25), _CFG)
    assert result.velocity_component == pytest.approx(1.0)
    assert result.velocity_ratio == pytest.approx(5.0)


def test_narrow_undirectional_spike_caps_at_amplifier_base() -> None:
    # Maximale Velocity, aber 1 Quelle (β=0.2) und kein direktionales
    # Sentiment (ω=0) → Score klar unter der Emissions-Schwelle 0.3+x.
    result = compute_hype_score(
        _inputs(
            mentions_recent=25,
            distinct_sources_recent=1,
            bullish_recent=0,
            bearish_recent=0,
        ),
        _CFG,
    )
    assert result.one_sidedness == 0.0
    assert result.score == pytest.approx(AMPLIFIER_BASE + 0.3 * 0.2)
    assert result.score < 0.5


def test_broad_one_sided_spike_reaches_full_score() -> None:
    result = compute_hype_score(
        _inputs(
            mentions_recent=25,
            distinct_sources_recent=5,
            bullish_recent=25,
            bearish_recent=0,
        ),
        _CFG,
    )
    assert result.score == pytest.approx(1.0)


def test_balanced_sentiment_reduces_score_vs_one_sided() -> None:
    one_sided = compute_hype_score(_inputs(bullish_recent=20, bearish_recent=0), _CFG)
    balanced = compute_hype_score(_inputs(bullish_recent=10, bearish_recent=10), _CFG)
    assert balanced.score < one_sided.score


# ── Kalte Baseline (frisch gelistetes Asset) ──────────────────────────────────


def test_cold_baseline_clamps_denominator_no_division_blowup() -> None:
    result = compute_hype_score(_inputs(mentions_baseline_total=0), _CFG)
    assert result.baseline_per_window == 1.0
    assert result.velocity_ratio == pytest.approx(20.0)
    assert result.velocity_component == pytest.approx(1.0)  # gesättigt, nicht ∞
    assert 0.0 < result.score <= 1.0


def test_determinism_same_inputs_same_score() -> None:
    a = compute_hype_score(_inputs(), _CFG)
    b = compute_hype_score(_inputs(), _CFG)
    assert a == b


# ── Aggregation (pure Bucketing) ──────────────────────────────────────────────


def _mention(
    hours_ago: float,
    *,
    source: str | None = "feed_a",
    label: str | None = "bullish",
    assets: tuple[str, ...] = ("BTC",),
    tz_aware: bool = True,
) -> DocMention:
    ts = _NOW - timedelta(hours=hours_ago)
    if not tz_aware:
        ts = ts.replace(tzinfo=None)
    return DocMention(observed_at=ts, source_name=source, sentiment_label=label, assets=assets)


def test_aggregate_buckets_recent_vs_baseline_correctly() -> None:
    mentions = [
        _mention(1.0),  # recent (≤ 6h)
        _mention(5.9),  # recent
        _mention(6.1),  # baseline
        _mention(24.0),  # baseline
        _mention(7 * 24 + 6.5),  # außerhalb des Baseline-Zeitraums → ignoriert
    ]
    out = aggregate_hype_inputs(mentions, now=_NOW, config=_CFG)
    assert out["BTC"].mentions_recent == 2
    assert out["BTC"].mentions_baseline_total == 2


def test_aggregate_counts_distinct_sources_and_sentiment_directions() -> None:
    mentions = [
        _mention(1.0, source="Feed_A", label="bullish"),
        _mention(2.0, source="feed_a", label="bullish"),  # gleiche Quelle (case)
        _mention(3.0, source="feed_b", label="bearish"),
        _mention(4.0, source=None, label="neutral"),  # keine Quelle, kein Richtungszähler
    ]
    out = aggregate_hype_inputs(mentions, now=_NOW, config=_CFG)
    inputs = out["BTC"]
    assert inputs.mentions_recent == 4
    assert inputs.distinct_sources_recent == 2
    assert inputs.bullish_recent == 2
    assert inputs.bearish_recent == 1


def test_aggregate_normalizes_asset_tags_and_skips_naive_timestamps() -> None:
    mentions = [
        _mention(1.0, assets=("btc", " ETH ")),
        _mention(2.0, assets=("BTC",), tz_aware=False),  # tz-naive → übersprungen
        _mention(3.0, assets=("",)),  # leerer Tag → ignoriert
    ]
    out = aggregate_hype_inputs(mentions, now=_NOW, config=_CFG)
    assert set(out.keys()) == {"BTC", "ETH"}
    assert out["BTC"].mentions_recent == 1
    assert out["ETH"].mentions_recent == 1


def test_aggregate_merges_pair_tickers_into_base_asset_bucket() -> None:
    # Die tickers-Spalte liefert Paare ("BTC/USDT"); crypto_assets ggf.
    # Base-Tags ("BTC") — beide müssen in EINEN Bucket fallen.
    mentions = [
        _mention(1.0, assets=("BTC/USDT",)),
        _mention(2.0, assets=("btc",)),
        _mention(3.0, assets=("BTC/USDC",)),
    ]
    out = aggregate_hype_inputs(mentions, now=_NOW, config=_CFG)
    assert set(out.keys()) == {"BTC"}
    assert out["BTC"].mentions_recent == 3


def test_aggregate_counts_double_tagged_document_once_per_asset() -> None:
    # Ein Dokument mit "BTC" UND "BTC/USDT" ist EINE Mention, nicht zwei.
    mentions = [_mention(1.0, assets=("BTC", "BTC/USDT", "ETH/USDT"))]
    out = aggregate_hype_inputs(mentions, now=_NOW, config=_CFG)
    assert out["BTC"].mentions_recent == 1
    assert out["ETH"].mentions_recent == 1


def test_aggregate_empty_input_yields_empty_dict() -> None:
    assert aggregate_hype_inputs([], now=_NOW, config=_CFG) == {}
