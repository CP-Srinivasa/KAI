"""Die Rechenregeln der Provenienz — einzeln geprüft, ohne Engine drumherum.

Der Verifier liest diese Felder als Wahrheit. Deshalb müssen die Regeln für sich
prüfbar sein und nicht nur als Nebenwirkung eines Fills.
"""

from __future__ import annotations

import math

import pytest

from app.execution.price_evidence import _age_ms_at_fill, _finite_or_none


@pytest.mark.parametrize("good", [0.0, 1.0, 1400.0, 1e9])
def test_endliche_nicht_negative_werte_bleiben(good: float) -> None:
    assert _finite_or_none(good) == good


@pytest.mark.parametrize(
    "bad",
    [float("nan"), math.inf, -math.inf, -0.1, -5.0, None, "1400", True, False, object()],
)
def test_alles_andere_wird_none(bad: object) -> None:
    """Ein nicht-endlicher Wert waere schlimmer als ein fehlender: er sieht aus
    wie eine Messung."""
    assert _finite_or_none(bad) is None


def test_age_rechnet_den_abstand_in_millisekunden() -> None:
    ms = _age_ms_at_fill("2026-08-21T09:00:00+00:00", "2026-08-21T09:00:02+00:00")
    assert ms == pytest.approx(2000.0)


def test_age_akzeptiert_das_z_suffix() -> None:
    assert _age_ms_at_fill("2026-08-21T09:00:00Z", "2026-08-21T09:00:01Z") == pytest.approx(1000.0)


@pytest.mark.parametrize(
    ("observed", "filled"),
    [
        ("", "2026-08-21T09:00:00+00:00"),
        ("2026-08-21T09:00:00+00:00", ""),
        ("kaputt", "2026-08-21T09:00:00+00:00"),
        ("2026-08-21T09:00:00+00:00", "kaputt"),
    ],
)
def test_unbrauchbare_zeitangaben_geben_none(observed: str, filled: str) -> None:
    assert _age_ms_at_fill(observed, filled) is None


def test_naive_zeitstempel_werden_abgelehnt() -> None:
    """Ohne Zeitzone ist der Abstand nicht belastbar — dann lieber keine Angabe."""
    assert _age_ms_at_fill("2026-08-21T09:00:00", "2026-08-21T09:00:02") is None


def test_beobachtung_nach_dem_fuellen_gibt_none() -> None:
    """Negative Alter sind keine Messung."""
    assert _age_ms_at_fill("2026-08-21T09:00:05+00:00", "2026-08-21T09:00:00+00:00") is None


def test_gleichzeitig_ist_null_und_bleibt_erhalten() -> None:
    assert _age_ms_at_fill("2026-08-21T09:00:00+00:00", "2026-08-21T09:00:00+00:00") == 0.0
