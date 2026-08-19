"""Der Cap ist Sensor, nicht Richter (P1a, 2026-08-19).

Ein Größenordnungs-Schwellwert kann Korruption nicht von einer echten Bewegung
unterscheiden — live gemessen fing er über den gesamten Audit-Stream null
Artefakte und drei echte Trades. Er löst deshalb nur noch eine Prüfung aus. Die
Rangfolge (exakte Signatur → Freispruch → Cap) ist dabei bindend.
"""

from __future__ import annotations

import pytest

from app.execution.close_classification import (
    EXTREME_MOVE_REASON,
    CloseVerdict,
    classify_close,
)
from app.learning.verified_real_closes import VERIFIED_REAL_CLOSES

_ENTRY_BY_FILL_ID = {
    "fill_fbd5580fab5c": 1.0011002999999998,
    "fill_f83be51981e1": 0.38524252499999995,
    "fill_446f84adb9e4": 1.7780761336479318,
}


def test_unauffaelliger_close_ist_clean() -> None:
    row = {
        "event_type": "position_closed",
        "symbol": "ETH/USDT",
        "entry_price": 1874.24956227636,
        "exit_price": 1901.3387412,
        "position_side": "long",
    }
    result = classify_close(row)
    assert result.verdict is CloseVerdict.CLEAN
    assert result.reason == ""


def test_extremer_move_verlangt_pruefung_statt_urteil() -> None:
    """Der Kern der Umstellung: kein "phantom", sondern offene Prüf-Schuld."""
    row = {
        "event_type": "position_closed",
        "symbol": "FOO/USDT",
        "entry_price": 1.0,
        "exit_price": 10.0,
        "position_side": "long",
    }
    result = classify_close(row)
    assert result.verdict is CloseVerdict.REQUIRES_VERIFICATION
    assert result.reason == EXTREME_MOVE_REASON
    assert result.needs_verification is True
    assert result.is_quarantined is False
    # Der Befund nennt die gemessene Groesse, behauptet aber keine Ursache.
    assert "%" in result.detail
    for verboten in ("phantom", "korrupt", "corrupt", "Artefakt"):
        assert verboten not in result.detail


def test_exakte_signatur_schlaegt_alles() -> None:
    row = {
        "event_type": "position_closed",
        "symbol": "ETH/USDT",
        "entry_price": 1874.24956227636,
        "exit_price": 3225.6863500000004,
        "position_side": "long",
    }
    result = classify_close(row)
    assert result.verdict is CloseVerdict.QUARANTINE
    assert result.reason == "mock_synthetic_exit_price"
    assert result.is_quarantined is True


def test_remediations_stempel_wird_quarantaeniert() -> None:
    row = {
        "event_type": "position_closed",
        "symbol": "SLX/USDT",
        "reason": "quarantine_off_venue_unpriceable",
        "entry_price": 0.54797385,
        "exit_price": 0.54797385,
        "position_side": "long",
    }
    result = classify_close(row)
    assert result.verdict is CloseVerdict.QUARANTINE
    assert result.reason == "quarantine_off_venue_unpriceable"


@pytest.mark.parametrize("record", VERIFIED_REAL_CLOSES, ids=lambda r: r.symbol)
def test_belegte_closes_sind_verified_nicht_nur_nicht_korrupt(record) -> None:
    """Ein geprüfter Close bekommt einen eigenen Zustand, kein stilles None."""
    row = {
        "event_type": "position_closed",
        "symbol": record.symbol,
        "timestamp_utc": record.timestamp_utc,
        "fill_id": record.fill_id,
        "order_id": record.order_id,
        "entry_price": _ENTRY_BY_FILL_ID[record.fill_id],
        "exit_price": record.exit_price,
        "position_side": "long",
    }
    result = classify_close(row)
    assert result.verdict is CloseVerdict.VERIFIED_MARKET_PLAUSIBLE
    assert result.is_quarantined is False
    assert result.needs_verification is False
    # Die Evidenz reist mit dem Urteil, nicht nur ein Flag.
    assert "Kerze" in result.detail


def test_signatur_schlaegt_auch_einen_freispruch() -> None:
    """Rangfolge: eine freigesprochene ID rettet keinen Mock-Preis."""
    record = VERIFIED_REAL_CLOSES[0]
    row = {
        "event_type": "position_closed",
        "symbol": "ETH/USDT",
        "timestamp_utc": record.timestamp_utc,
        "fill_id": record.fill_id,
        "order_id": record.order_id,
        "entry_price": 1874.24956227636,
        "exit_price": 3225.6863500000004,
        "position_side": "long",
    }
    assert classify_close(row).verdict is CloseVerdict.QUARANTINE


def test_zeile_ohne_preise_ist_clean_nicht_verdaechtig() -> None:
    """Konservativ: was sich nicht beurteilen lässt, wird nicht beschuldigt."""
    row = {"event_type": "position_closed", "symbol": "XRP/USDT", "trade_pnl_usd": 1.0}
    assert classify_close(row).verdict is CloseVerdict.CLEAN


def test_uebergang_aggregatoren_halten_ungepruefte_closes_weiterhin_heraus() -> None:
    """Bis der Verifier steht, bleibt REQUIRES_VERIFICATION aus den Kennzahlen.

    Ungeprüft in eine Buch-Zahl zu geben wäre die schlechtere Richtung — aber
    unter eigenem Label, damit die Prüf-Schuld messbar ist.
    """
    from app.learning.bayes_quarantine import corruption_reason, is_corrupt_close

    row = {
        "event_type": "position_closed",
        "symbol": "FOO/USDT",
        "entry_price": 1.0,
        "exit_price": 10.0,
        "position_side": "long",
    }
    assert corruption_reason(row) == EXTREME_MOVE_REASON
    assert is_corrupt_close(row) is True
