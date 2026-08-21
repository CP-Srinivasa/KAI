"""Das Evidenz-Artefakt: der Hash darf nur am Inhalt hängen, an nichts sonst."""

from __future__ import annotations

import json

import pytest

from app.execution.close_evidence import (
    EVIDENCE_SCHEMA_VERSION,
    CloseEvidence,
    VenueCandle,
    canonical_bytes,
    canonical_sha256,
)

MINUTE = 60_000
BASE_MS = 1_755_000_000_000


def _evidence(**kw) -> CloseEvidence:
    defaults = {
        "close_fill_id": "fill_abc",
        "close_order_id": "ord_abc",
        "symbol": "ETH/USDT",
        "close_timestamp_utc": "2026-08-21T09:00:30+00:00",
        "venue": "bybit",
        "interval": "1m",
        "window_start_ms": BASE_MS,
        "window_end_ms": BASE_MS + 5 * MINUTE,
        "candles": (VenueCandle(BASE_MS, 100.0, 101.0, 99.0, 100.5),),
        "collected_at_utc": "2026-08-21T09:05:00+00:00",
        "collector_code_sha": "c0ffee",
    }
    defaults.update(kw)
    return CloseEvidence(**defaults)


# --- kanonische Bytes -----------------------------------------------------------


def test_schluesselreihenfolge_aendert_den_hash_nicht() -> None:
    a = {"b": 2, "a": 1, "c": [3, 4]}
    b = {"c": [3, 4], "a": 1, "b": 2}
    assert canonical_sha256(a) == canonical_sha256(b)


def test_formatierung_aendert_den_hash_nicht() -> None:
    """Der Hash darf sich nicht aendern, weil jemand die Datei huebscher macht."""
    payload = {"a": 1, "b": "x"}
    hübsch = json.loads(json.dumps(payload, indent=4))
    assert canonical_sha256(payload) == canonical_sha256(hübsch)


def test_kanonische_bytes_haben_keinen_leerraum() -> None:
    assert canonical_bytes({"a": 1, "b": 2}) == b'{"a":1,"b":2}'


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_nan_und_inf_brechen_die_serialisierung(bad: float) -> None:
    """Sonst stuende ``NaN`` im JSON und saehe spaeter wie eine Messung aus."""
    with pytest.raises(ValueError):
        canonical_bytes({"x": bad})


def test_hash_haengt_am_inhalt() -> None:
    one = _evidence()
    other = _evidence(candles=(VenueCandle(BASE_MS, 100.0, 102.0, 99.0, 100.5),))
    assert one.payload_sha256() != other.payload_sha256()
    assert one.payload_sha256() == _evidence().payload_sha256()


def test_artefakt_traegt_schema_und_sammlerversion() -> None:
    ev = _evidence()
    assert ev.schema_version == EVIDENCE_SCHEMA_VERSION
    assert ev.collector_code_sha == "c0ffee"
    payload = ev.as_payload()
    assert payload["schema_version"] == EVIDENCE_SCHEMA_VERSION
    assert payload["collector_code_sha"] == "c0ffee"


# --- Kerzen ----------------------------------------------------------------------


def test_kerze_deckt_den_zeitpunkt_ab() -> None:
    ev = _evidence(
        candles=(
            VenueCandle(BASE_MS, 100.0, 101.0, 99.0, 100.5),
            VenueCandle(BASE_MS + MINUTE, 100.5, 103.0, 100.0, 102.0),
        )
    )
    assert ev.candle_covering(BASE_MS + 30_000).high == 101.0
    assert ev.candle_covering(BASE_MS + MINUTE + 10).high == 103.0
    assert ev.candle_covering(BASE_MS - 1) is None


def test_unbekanntes_intervall_gibt_keine_kerze() -> None:
    ev = _evidence(interval="7m")
    assert ev.candle_covering(BASE_MS + 10) is None


def test_leeres_artefakt_ist_fehlende_evidenz() -> None:
    ev = _evidence(candles=())
    assert ev.is_empty is True
    assert ev.band() is None


def test_band_spannt_ueber_alle_kerzen() -> None:
    ev = _evidence(
        candles=(
            VenueCandle(BASE_MS, 100.0, 101.0, 99.0, 100.5),
            VenueCandle(BASE_MS + MINUTE, 100.5, 105.0, 98.0, 102.0),
        )
    )
    assert ev.band() == (98.0, 105.0)


def test_kerze_prueft_mit_toleranz_nicht_bit_exakt() -> None:
    """Externe Marktdaten nie bit-exakt vergleichen."""
    candle = VenueCandle(BASE_MS, 100.0, 101.0, 99.0, 100.5)
    assert candle.contains(101.0) is True
    assert candle.contains(101.02) is False
    assert candle.contains(101.02, tolerance_pct=0.05) is True
