r"""Das eingefrorene Auswertungs-Datenset — Bytes, nicht Absichten.

Ein Verdikt ist nur dann nachpruefbar, wenn spaeter feststellbar ist, WORAUF es
gefallen ist. Dafuer reicht kein Verweis auf "die Daten von damals": es braucht
Bytes mit einem Hash. Dieses Datenset ist genau das — und es verweigert sich in
jedem Fall, in dem es still etwas anderes waere, als es behauptet:

* eine andere Symbolmenge oder -reihenfolge als das versiegelte Universum,
* ein Signal vor T0 (das waere In-Sample),
* ein Label, das am Checkpoint noch nicht ausgelaufen ist (Blick nach vorn),
* ``NaN``/``Inf``, die JSON nicht kanonisch darstellen kann,
* ``None``, das zu ``0.0`` verwaschen wird — ``None`` heisst DATA_UNAVAILABLE
  und ist eine Aussage, kein fehlender Wert.
"""

from __future__ import annotations

import math

import pytest

from app.analysis.features.feature_matrix import FeatureRow
from app.research.frozen_evaluation import (
    FrozenSymbolPanel,
    build_frozen_dataset,
    dataset_sha256,
)

_T0 = "2026-09-01T00:00:00+00:00"
_T1 = "2026-11-30T00:00:00+00:00"
_SYMBOLS = ("BTC/USDT", "ETH/USDT")
_UNIVERSE_SHA = "u" * 64


def _row(ts: str, close: float = 100.0, rsi: float | None = 30.0) -> FeatureRow:
    return FeatureRow(
        timestamp_utc=ts,
        close=close,
        log_return=0.001,
        rsi_14=rsi,
        adx_14=None,
        plus_di_14=None,
        minus_di_14=None,
        realized_vol_24=None,
        ema_12=None,
        ema_26=None,
        macd=None,
        bollinger_z_20=None,
    )


def _panel(
    symbol: str,
    *,
    ts: str = "2026-09-02T00:00:00+00:00",
    label: float | None = 12.5,
    exit_utc: str | None = "2026-09-02T04:00:00+00:00",
    rsi: float | None = 30.0,
) -> FrozenSymbolPanel:
    return FrozenSymbolPanel(
        symbol=symbol,
        rows=(_row(ts, rsi=rsi),),
        labels=(label,),
        label_exit_utc=(exit_utc,),
    )


def _build(**overrides):
    kwargs = {
        "canonical_symbols": _SYMBOLS,
        "panels": tuple(_panel(s) for s in _SYMBOLS),
        "universe_sha256": _UNIVERSE_SHA,
        "t0_utc": _T0,
        "checkpoint": "T1",
        "checkpoint_cutoff_utc": _T1,
    }
    kwargs.update(overrides)
    return build_frozen_dataset(**kwargs)


# ── Identitaet ──────────────────────────────────────────────────────────────


def test_a_wellformed_dataset_has_a_stable_hash() -> None:
    first = _build()
    second = _build()

    assert first.dataset_sha256 == second.dataset_sha256
    assert len(first.dataset_sha256) == 64


def test_symbol_order_is_part_of_the_identity() -> None:
    """Dieselben Daten in anderer Reihenfolge sind ein anderes Datenset.

    Sonst koennte man nachtraeglich umsortieren und denselben Hash behaupten.
    """
    reversed_symbols = tuple(reversed(_SYMBOLS))

    other = _build(
        canonical_symbols=reversed_symbols,
        panels=tuple(_panel(s) for s in reversed_symbols),
    )

    assert other.dataset_sha256 != _build().dataset_sha256


def test_a_changed_feature_changes_the_hash() -> None:
    changed = _build(panels=(_panel("BTC/USDT", rsi=31.0), _panel("ETH/USDT")))

    assert changed.dataset_sha256 != _build().dataset_sha256


def test_none_is_preserved_as_data_unavailable_not_zero() -> None:
    """``None`` und ``0.0`` duerfen niemals denselben Hash ergeben."""
    with_none = _build(panels=(_panel("BTC/USDT", rsi=None), _panel("ETH/USDT")))
    with_zero = _build(panels=(_panel("BTC/USDT", rsi=0.0), _panel("ETH/USDT")))

    assert with_none.dataset_sha256 != with_zero.dataset_sha256


def test_the_hash_is_computed_over_the_published_bytes() -> None:
    dataset = _build()

    assert dataset_sha256(dataset.canonical_bytes) == dataset.dataset_sha256


# ── Verweigerungen ──────────────────────────────────────────────────────────


def test_a_missing_symbol_is_refused() -> None:
    with pytest.raises(ValueError, match="universe"):
        _build(panels=(_panel("BTC/USDT"),))


def test_a_foreign_symbol_is_refused() -> None:
    with pytest.raises(ValueError, match="universe"):
        _build(panels=(_panel("BTC/USDT"), _panel("DOGE/USDT")))


def test_panels_out_of_canonical_order_are_refused() -> None:
    """Die Reihenfolge wird nicht stillschweigend korrigiert — sie wird geprueft."""
    with pytest.raises(ValueError, match="order"):
        _build(panels=(_panel("ETH/USDT"), _panel("BTC/USDT")))


def test_a_signal_before_t0_is_refused() -> None:
    """Vor T0 ist In-Sample. Ein einziges solches Signal entwertet den Test."""
    with pytest.raises(ValueError, match="T0"):
        _build(panels=(_panel("BTC/USDT", ts="2026-08-31T23:00:00+00:00"), _panel("ETH/USDT")))


def test_a_signal_exactly_at_t0_is_allowed() -> None:
    dataset = _build(panels=(_panel("BTC/USDT", ts=_T0), _panel("ETH/USDT")))

    assert len(dataset.dataset_sha256) == 64


def test_a_label_that_has_not_matured_at_the_checkpoint_is_refused() -> None:
    """Ein Label, dessen Fenster noch laeuft, ist ein Blick nach vorn."""
    with pytest.raises(ValueError, match="matur"):
        _build(
            panels=(
                _panel("BTC/USDT", exit_utc="2026-12-01T00:00:00+00:00"),
                _panel("ETH/USDT"),
            )
        )


def test_a_label_exiting_exactly_at_the_checkpoint_is_mature() -> None:
    dataset = _build(panels=(_panel("BTC/USDT", exit_utc=_T1), _panel("ETH/USDT")))

    assert len(dataset.dataset_sha256) == 64


def test_an_unavailable_label_needs_no_exit_timestamp() -> None:
    """``None`` heisst: es gab kein Label. Dann gibt es auch nichts zu reifen."""
    dataset = _build(panels=(_panel("BTC/USDT", label=None, exit_utc=None), _panel("ETH/USDT")))

    assert len(dataset.dataset_sha256) == 64


def test_a_present_label_without_exit_timestamp_is_refused() -> None:
    with pytest.raises(ValueError, match="label_exit"):
        _build(panels=(_panel("BTC/USDT", exit_utc=None), _panel("ETH/USDT")))


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_nan_and_inf_are_refused_not_canonicalised(bad: float) -> None:
    """JSON kennt kein kanonisches ``NaN``. Stillschweigend zu ``null`` zu
    wandeln waere DATA_UNAVAILABLE behauptet, wo ein Rechenfehler steht."""
    assert math.isnan(bad) or math.isinf(bad)

    with pytest.raises(ValueError, match="finite"):
        _build(panels=(_panel("BTC/USDT", label=bad), _panel("ETH/USDT")))


def test_misaligned_rows_and_labels_are_refused() -> None:
    panel = FrozenSymbolPanel(
        symbol="BTC/USDT",
        rows=(_row("2026-09-02T00:00:00+00:00"), _row("2026-09-03T00:00:00+00:00")),
        labels=(1.0,),
        label_exit_utc=("2026-09-02T04:00:00+00:00",),
    )

    with pytest.raises(ValueError, match="aligned"):
        _build(panels=(panel, _panel("ETH/USDT")))
