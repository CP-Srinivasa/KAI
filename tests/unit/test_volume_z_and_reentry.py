r"""Teil B der Spec 2026-08-19: Volumen in die bestehende Research-Pipeline.

Geprueft wird NICHT "``volume_z_20`` existiert", sondern die vier Eigenschaften,
ohne die das Feature in einer Truth-Infrastruktur nichts verloren hat: zeitlich
korrekt, deterministisch, ohne Lookahead, und niemals ``NaN``.

Der ``NaN``-Punkt ist Korrektheit, nicht Stil. Jeder bestehende Decider schuetzt
sich mit ``is not None`` — und ``NaN is not None`` ist **True**. Ein ``NaN``
passierte den Guard ungehindert, alle folgenden Vergleiche lieferten ``False``,
die Regel gaebe scheinbar korrekt ``0`` zurueck: zufaellig richtig, nicht
absichtlich. Schlimmer, ``NaN`` propagiert lautlos durch jede Aggregation
(``fmean([1, 2, NaN]) -> nan``).

Dazu die Ausfuehrungskonvention aus C3, an konkreten Zeitstempeln gepinnt statt
an einer Formel — zwei gleich plausible Lesarten liegen um eine volle Kerze
auseinander.
"""

from __future__ import annotations

import math
import statistics

import pytest

from app.analysis.features.feature_matrix import FeatureRow, build_feature_matrix
from app.analysis.features.forward_returns import (
    compute_forward_return_bps,
    compute_next_open_forward_return_bps,
)
from app.analysis.indicators.volume_z import (
    VOLUME_SPIKE_Z,
    VOLUME_Z_WINDOW,
    compute_volume_z,
)
from app.market_data.models import OHLCV
from app.research.runner import (
    PRIMARY_CONFIRMATORY_NAME,
    default_hypotheses,
    primary_confirmatory_hypothesis,
    rsi_reentry_volume_confirmed,
    secondary_benchmark_family,
)


def _varied(n: int) -> list[float]:
    """Volumen mit echter Streuung — konstante Reihen haben sigma=0 und sind None."""
    return [1000.0 + (i % 7) * 13.0 for i in range(n)]


# ── volume_z_20: die Formel ─────────────────────────────────────────────────


def test_matches_the_frozen_formula_exactly() -> None:
    """Gegen die versiegelte Definition, nicht gegen sich selbst nachgerechnet."""
    volumes = _varied(25)
    volumes[24] = 9000.0

    z = compute_volume_z(volumes, VOLUME_Z_WINDOW)

    baseline = [math.log1p(v) for v in volumes[4:24]]
    expected = (math.log1p(9000.0) - statistics.fmean(baseline)) / statistics.pstdev(baseline)
    assert z[24] == pytest.approx(expected)


def test_baseline_is_strictly_the_previous_bars_no_lookahead() -> None:
    """Der Kern-Lookahead-Test: die Kerze darf nicht in ihre eigene Referenz.

    Wuerde die aktuelle Kerze in die Baseline geraten, daempfte sie genau die
    Abweichung, die das Feature messen soll — und der Test hier wuerde rot.
    """
    volumes = _varied(22)
    z_before = compute_volume_z(volumes, VOLUME_Z_WINDOW)

    # Nur die AKTUELLE Kerze aendern. Waere sie Teil ihrer eigenen Baseline,
    # aenderte sich auch mu/sigma und der Z-Wert waere nicht die reine Differenz.
    volumes[21] = 50_000.0
    z_after = compute_volume_z(volumes, VOLUME_Z_WINDOW)

    baseline = [math.log1p(v) for v in volumes[1:21]]
    expected = (math.log1p(50_000.0) - statistics.fmean(baseline)) / statistics.pstdev(baseline)

    assert z_after[21] == pytest.approx(expected)
    assert z_after[20] == z_before[20], "eine spaetere Kerze darf eine fruehere nicht veraendern"


def test_future_bars_never_affect_an_earlier_value() -> None:
    """Kausalitaet als Ganzes: alles nach i ist fuer i irrelevant."""
    volumes = _varied(40)
    full = compute_volume_z(volumes, VOLUME_Z_WINDOW)
    truncated = compute_volume_z(volumes[:30], VOLUME_Z_WINDOW)

    assert full[:30] == truncated


def test_warm_up_is_none_not_zero() -> None:
    """Waehrend der Aufwaermphase gibt es keinen Wert — nicht den Wert null."""
    z = compute_volume_z(_varied(30), VOLUME_Z_WINDOW)

    assert all(value is None for value in z[:VOLUME_Z_WINDOW])
    assert z[VOLUME_Z_WINDOW] is not None


def test_zero_dispersion_is_none() -> None:
    """Ein konstantes Volumenregime hat keinen sinnvollen Extremwert."""
    assert compute_volume_z([1000.0] * 30, VOLUME_Z_WINDOW) == [None] * 30


def test_invalid_volume_poisons_only_the_windows_it_belongs_to() -> None:
    """Ein Datendefekt darf weder ersetzt noch stillschweigend uebersprungen werden."""
    volumes = _varied(45)
    volumes[25] = float("nan")

    z = compute_volume_z(volumes, VOLUME_Z_WINDOW)

    assert z[25] is None, "die defekte Kerze selbst hat keinen Wert"
    assert all(z[i] is None for i in range(26, 45)), "und jede Baseline, die sie enthaelt"


def test_negative_volume_is_a_defect_not_a_small_number() -> None:
    volumes = _varied(25)
    volumes[24] = -1.0

    assert compute_volume_z(volumes, VOLUME_Z_WINDOW)[24] is None


def test_output_is_never_nan_or_infinite() -> None:
    """Die Eigenschaft, an der der ``is not None``-Guard sonst zerbricht."""
    volumes = _varied(200)
    volumes[100] = 0.0
    volumes[150] = 1e18

    for value in compute_volume_z(volumes, VOLUME_Z_WINDOW):
        assert value is None or math.isfinite(value)


def test_window_below_two_is_rejected() -> None:
    with pytest.raises(ValueError, match="window must be >= 2"):
        compute_volume_z(_varied(10), 1)


# ── FeatureRow: durchgereicht, aber die Grenze haelt ────────────────────────


def _candles(n: int, *, volumes: list[float] | None = None) -> list[OHLCV]:
    vols = volumes if volumes is not None else _varied(n)
    return [
        OHLCV(
            symbol="BTC/USDT",
            timestamp_utc=f"2026-01-{1 + i // 24:02d}T{i % 24:02d}:00:00+00:00",
            timeframe="1h",
            open=100.0 + i,
            high=101.0 + i,
            low=99.0 + i,
            close=100.5 + i,
            volume=vols[i],
        )
        for i in range(n)
    ]


def test_feature_row_carries_volume_z_and_previous_rsi() -> None:
    rows = build_feature_matrix(_candles(40))

    assert rows[25].volume_z_20 is not None
    for i in range(1, 40):
        assert rows[i].rsi_14_prev == rows[i - 1].rsi_14
    assert rows[0].rsi_14_prev is None, "die erste Kerze hat keinen Vorgaenger"


def test_open_stays_out_of_the_feature_row() -> None:
    """Die Integritaetsgrenze zwischen Merkmal und Label, ausdruecklich gepinnt.

    Der Einstiegs-Open liegt zeitlich HINTER der Entscheidung. Waere er einem
    Decider zugaenglich, waere die Lookahead-Grenze der Feature-Matrix ("Zeile i
    haengt nur von candles[0..i] ab") aufgeweicht — und zwar auf eine Weise, die
    kein Backtest mehr sichtbar machen wuerde.
    """
    fields = set(FeatureRow.__dataclass_fields__)

    assert "open" not in fields
    assert not any(name.endswith("_open") or name.startswith("open_") for name in fields)


def test_matrix_is_deterministic() -> None:
    candles = _candles(60)

    assert build_feature_matrix(candles) == build_feature_matrix(candles)


# ── Ausfuehrungskonvention: Golden-Test mit echten Zeitstempeln ─────────────


def test_next_open_label_golden_case_h4_hourly() -> None:
    """Signal 12:00-12:59 -> Entry OPEN 13:00 -> Exit CLOSE 16:59.

    Die naheliegende Fehllesart waere ``EXIT = t + 1 + h``, also Close 17:59 —
    eine Stunde zu spaet. Deshalb wird hier an konkreten Zeitstempeln und
    unterscheidbaren Preisen gepinnt, nicht an der Formel.
    """
    bars = [
        OHLCV(
            symbol="BTC/USDT",
            timestamp_utc=f"2026-03-04T{hour:02d}:00:00+00:00",
            timeframe="1h",
            open=100.0 + 10.0 * (hour - 12),
            high=200.0,
            low=50.0,
            close=101.0 + 10.0 * (hour - 12),
            volume=1000.0,
        )
        for hour in range(12, 18)
    ]
    opens = [bar.open for bar in bars]
    closes = [bar.close for bar in bars]
    signal, horizon = 0, 4

    label = compute_next_open_forward_return_bps(opens, closes, horizon=horizon)

    entry_index = signal + 1
    exit_index = signal + horizon
    # Die Konvention wird an Zeitstempeln festgenagelt, nicht an Indizes:
    assert bars[signal].timestamp_utc.startswith("2026-03-04T12:00")
    assert bars[entry_index].timestamp_utc.startswith("2026-03-04T13:00")
    assert bars[exit_index].timestamp_utc.startswith("2026-03-04T16:00")

    assert label[signal] == pytest.approx(
        10_000.0 * (closes[exit_index] / opens[entry_index] - 1.0)
    )

    # Und ausdruecklich NICHT die Fehllesart EXIT = t + 1 + h (Close 17:59):
    wrong = 10_000.0 * (closes[signal + 1 + horizon] / opens[entry_index] - 1.0)
    assert label[signal] != pytest.approx(wrong)


def test_next_open_differs_from_close_to_close() -> None:
    """Waeren beide gleich, waere die neue Konvention wirkungslos — und der
    Golden-Test bewiese nichts."""
    opens = [100.0, 110.0, 120.0, 130.0, 140.0, 150.0]
    closes = [101.0, 111.0, 121.0, 131.0, 141.0, 151.0]

    next_open = compute_next_open_forward_return_bps(opens, closes, horizon=4)
    close_close = compute_forward_return_bps(closes, horizon=4)

    assert next_open[0] != pytest.approx(close_close[0])


def test_next_open_tail_has_no_label() -> None:
    opens = [100.0] * 6
    closes = [101.0] * 6

    label = compute_next_open_forward_return_bps(opens, closes, horizon=4)

    assert label[-4:] == [None, None, None, None]


def test_next_open_rejects_bad_arguments() -> None:
    with pytest.raises(ValueError, match="horizon must be >= 1"):
        compute_next_open_forward_return_bps([1.0], [1.0], 0)
    with pytest.raises(ValueError, match="equal length"):
        compute_next_open_forward_return_bps([1.0, 2.0], [1.0], 1)


def test_close_to_close_labeling_is_untouched() -> None:
    """Die zwoelf versiegelten Verdikte haengen daran — ihre Konvention bleibt."""
    closes = [100.0, 110.0, 120.0, 130.0, 140.0]

    assert compute_forward_return_bps(closes, 2)[0] == pytest.approx(
        10_000.0 * (120.0 / 100.0 - 1.0)
    )


# ── Die Hypothese ───────────────────────────────────────────────────────────


def _row(*, rsi: float | None, rsi_prev: float | None, vol_z: float | None) -> FeatureRow:
    return FeatureRow(
        timestamp_utc="2026-01-01T00:00:00+00:00",
        close=100.0,
        log_return=None,
        rsi_14=rsi,
        adx_14=None,
        plus_di_14=None,
        minus_di_14=None,
        realized_vol_24=None,
        ema_12=None,
        ema_26=None,
        macd=None,
        bollinger_z_20=None,
        rsi_14_prev=rsi_prev,
        volume_z_20=vol_z,
    )


def test_long_on_upward_crossing_with_spike() -> None:
    assert rsi_reentry_volume_confirmed(_row(rsi_prev=28.0, rsi=31.0, vol_z=2.5)) == 1


def test_short_on_downward_crossing_with_spike() -> None:
    assert rsi_reentry_volume_confirmed(_row(rsi_prev=72.0, rsi=69.0, vol_z=2.5)) == -1


def test_it_is_a_transition_not_a_level() -> None:
    """Der Unterschied zu ``rsi_oversold_long`` (-23,27 bps), der die Regel neu macht.

    Die Level-Regel feuert in JEDER Kerze unterhalb 30. Diese feuert genau einmal
    beim Uebergang — bei einem Buch, dessen Verlust fast vollstaendig Gebuehr ist,
    ist das der entscheidende Unterschied.
    """
    deep_in_the_zone = _row(rsi_prev=22.0, rsi=25.0, vol_z=5.0)

    assert rsi_reentry_volume_confirmed(deep_in_the_zone) == 0


def test_no_trade_without_the_volume_spike() -> None:
    """Die Konjunktion ist das einzig Neue — ohne sie ist es die widerlegte Regel."""
    assert rsi_reentry_volume_confirmed(_row(rsi_prev=28.0, rsi=31.0, vol_z=1.99)) == 0
    assert rsi_reentry_volume_confirmed(_row(rsi_prev=28.0, rsi=31.0, vol_z=VOLUME_SPIKE_Z)) == 1


def test_boundary_is_inclusive_on_the_re_entry_side() -> None:
    """``rsi_14 >= 30`` — exakt 30,0 ist bereits ein Re-Entry, kein Grenzfall."""
    assert rsi_reentry_volume_confirmed(_row(rsi_prev=29.9, rsi=30.0, vol_z=3.0)) == 1
    assert rsi_reentry_volume_confirmed(_row(rsi_prev=70.1, rsi=70.0, vol_z=3.0)) == -1


@pytest.mark.parametrize(
    ("rsi", "rsi_prev", "vol_z"),
    [
        (None, 28.0, 3.0),
        (31.0, None, 3.0),
        (31.0, 28.0, None),
    ],
)
def test_missing_feature_means_no_trade(rsi, rsi_prev, vol_z) -> None:
    """Der Runner-Vertrag: "None features (warm-up) map to 0 (no trade)"."""
    assert rsi_reentry_volume_confirmed(_row(rsi=rsi, rsi_prev=rsi_prev, vol_z=vol_z)) == 0


# ── Familien-Disziplin ──────────────────────────────────────────────────────


def test_the_sealed_family_of_twelve_is_not_extended() -> None:
    """Ein dreizehntes Mitglied wuerde die BH-Huerde der zwoelf ruckwirkend heben.

    Deren Verdikte sind versiegelt. Sie umzuetikettieren, weil ein neuer Test
    dazukommt, waere nachtraegliche Kriterienaenderung — das neue Experiment
    bekommt seine eigene eingefrorene Familie.
    """
    names = [name for name, _ in default_hypotheses()]

    assert len(names) == 12
    assert PRIMARY_CONFIRMATORY_NAME not in names


def test_primary_family_has_exactly_one_member() -> None:
    """m=1 ist hier das korrekte Verfahren, nicht schwacher Schutz.

    Steht vor T0 genau eine Primaerhypothese fest und wird sie ausschliesslich
    auf neuen Forward-OOS-Daten beurteilt, existiert innerhalb des Experiments
    kein Multiple-Testing-Problem; BH-FDR reduziert sich rechnerisch auf
    ``p <= alpha``.
    """
    primary = primary_confirmatory_hypothesis()

    assert len(primary) == 1
    assert primary[0][0] == PRIMARY_CONFIRMATORY_NAME


def test_secondary_benchmark_is_the_twelve_plus_one() -> None:
    names = [name for name, _ in secondary_benchmark_family()]

    assert len(names) == 13
    assert names[:12] == [name for name, _ in default_hypotheses()]
    assert names[12] == PRIMARY_CONFIRMATORY_NAME
