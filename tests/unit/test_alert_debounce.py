"""Entprellung wiederholter Alerts (V10).

Die Messung vom 2026-09-16 (Daily Review, n = 293 dispatchte
``tradingview_webhook``-Alerts vom 09.-16.09.) ergab: der Abstand zwischen zwei
Alerts gleicher ``(asset, sentiment)`` hat den Median 1,08 min, 75,6 % aller
Wiederholungen kommen innerhalb von zwei Minuten, und die Trefferquote faellt
monoton mit der Position in der Serie -- erster Alert 51,9 %, ab dem 21. noch
13,7 %. Entprellt man bei 60 Minuten, bleiben 41 von 293 Alerts uebrig; die
Behaltenen treffen zu 51,6 %, die Weggefallenen zu 21,1 %.

Diese Tests pinnen die Semantik, nicht die Messung:

* Ein Richtungswechsel geht IMMER sofort durch. Ein zweiter Alert ist nur dann
  eine Wiederholung, wenn er dieselbe Richtung fuer denselben Wert meldet.
* Was nicht sauber verschluesselt werden kann, wird durchgelassen (fail-open).
  Ein unterdrueckter echter Alert ist teurer als ein doppelter.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.alerts.alert_debounce import (
    DEBOUNCE_BLOCK_REASON,
    DEFAULT_WINDOW_MINUTES,
    AlertDebouncer,
    debounce_window_from_env,
    seed_debouncer_from_audit_rows,
)

T0 = datetime(2026, 9, 15, 5, 0, 0, tzinfo=UTC)


def _d(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def _mk(window_minutes: int = 60) -> AlertDebouncer:
    return AlertDebouncer(window=timedelta(minutes=window_minutes))


# --------------------------------------------------------------------------
# Grundfall
# --------------------------------------------------------------------------


def test_erster_alert_geht_immer_durch() -> None:
    dec = _mk().decide("tradingview_webhook", "ETH", "bullish", now=T0)
    assert dec.emit is True
    assert dec.reason == ""
    assert dec.repeat_index == 0


def test_wiederholung_im_fenster_wird_unterdrueckt() -> None:
    deb = _mk()
    assert deb.decide("tradingview_webhook", "ETH", "bullish", now=T0).emit is True
    dec = deb.decide("tradingview_webhook", "ETH", "bullish", now=_d(1))
    assert dec.emit is False
    assert dec.reason == DEBOUNCE_BLOCK_REASON
    assert dec.repeat_index == 1
    assert dec.last_emitted_at == T0


def test_serie_zaehlt_hoch_und_bleibt_unterdrueckt() -> None:
    """Der 59-Alert-Ausschlag vom 15.09. 05:00Z: einer pro Minute, eine Stunde lang."""
    deb = _mk()
    emitted = [
        deb.decide("tradingview_webhook", "ETH", "bullish", now=_d(i)).emit for i in range(59)
    ]
    assert emitted[0] is True
    assert not any(emitted[1:]), "nur der erste Alert der Serie darf durch"
    assert sum(emitted) == 1


def test_nach_dem_fenster_geht_wieder_einer_durch() -> None:
    deb = _mk()
    deb.decide("tradingview_webhook", "ETH", "bullish", now=T0)
    dec = deb.decide("tradingview_webhook", "ETH", "bullish", now=_d(60.5))
    assert dec.emit is True
    assert dec.repeat_index == 0, "eine neue Serie zaehlt wieder bei null"


def test_genau_auf_der_fenstergrenze_bleibt_unterdrueckt() -> None:
    """Die Grenze ist strikt: erst JENSEITS des Fensters geht wieder einer durch.

    Die Trockenrechnung hat mit ``> fenster`` gerechnet; eine Verschiebung auf
    ``>=`` wuerde die gemessene Reduktion stillschweigend aendern.
    """
    deb = _mk()
    deb.decide("tradingview_webhook", "ETH", "bullish", now=T0)
    assert deb.decide("tradingview_webhook", "ETH", "bullish", now=_d(60)).emit is False


# --------------------------------------------------------------------------
# Richtungswechsel -- der Fall, den die Livedaten NICHT enthalten
# --------------------------------------------------------------------------


def test_richtungswechsel_geht_sofort_durch() -> None:
    """Seit 16.04. sind 3300 von 3301 TV-Alerts bullish. Der Flip ist an den
    Livedaten nicht pruefbar und daher hier festgeschrieben."""
    deb = _mk()
    deb.decide("tradingview_webhook", "ETH", "bullish", now=T0)
    dec = deb.decide("tradingview_webhook", "ETH", "bearish", now=_d(1))
    assert dec.emit is True
    assert dec.reason == ""


def test_rueckwechsel_geht_ebenfalls_durch() -> None:
    deb = _mk()
    deb.decide("tradingview_webhook", "ETH", "bullish", now=T0)
    deb.decide("tradingview_webhook", "ETH", "bearish", now=_d(1))
    assert deb.decide("tradingview_webhook", "ETH", "bullish", now=_d(2)).emit is True


def test_nach_dem_flip_wird_die_neue_richtung_entprellt() -> None:
    deb = _mk()
    deb.decide("tradingview_webhook", "ETH", "bullish", now=T0)
    deb.decide("tradingview_webhook", "ETH", "bearish", now=_d(1))
    assert deb.decide("tradingview_webhook", "ETH", "bearish", now=_d(2)).emit is False


# --------------------------------------------------------------------------
# Trennung der Schluessel
# --------------------------------------------------------------------------


def test_verschiedene_werte_sind_unabhaengig() -> None:
    deb = _mk()
    deb.decide("tradingview_webhook", "ETH", "bullish", now=T0)
    assert deb.decide("tradingview_webhook", "BTC", "bullish", now=_d(1)).emit is True


def test_verschiedene_quellen_sind_unabhaengig() -> None:
    deb = _mk()
    deb.decide("tradingview_webhook", "ETH", "bullish", now=T0)
    assert deb.decide("telegram", "ETH", "bullish", now=_d(1)).emit is True


def test_wert_wird_gross_klein_unabhaengig_verschluesselt() -> None:
    deb = _mk()
    deb.decide("tradingview_webhook", "ETH", "bullish", now=T0)
    assert deb.decide("tradingview_webhook", "eth", "BULLISH", now=_d(1)).emit is False


# --------------------------------------------------------------------------
# Fail-open
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "asset", "sentiment"),
    [
        ("tradingview_webhook", None, "bullish"),
        ("tradingview_webhook", "ETH", None),
        (None, "ETH", "bullish"),
        ("tradingview_webhook", "", "bullish"),
        ("tradingview_webhook", "ETH", "   "),
    ],
)
def test_unvollstaendiger_schluessel_geht_durch(source, asset, sentiment) -> None:
    """Was nicht verschluesselt werden kann, wird nicht unterdrueckt."""
    deb = _mk()
    first = deb.decide(source, asset, sentiment, now=T0)
    second = deb.decide(source, asset, sentiment, now=_d(1))
    assert first.emit is True
    assert second.emit is True, "ohne Schluessel gibt es keine Wiederholung"
    assert second.reason == ""


def test_fenster_null_schaltet_die_entprellung_ab() -> None:
    deb = _mk(window_minutes=0)
    deb.decide("tradingview_webhook", "ETH", "bullish", now=T0)
    assert deb.decide("tradingview_webhook", "ETH", "bullish", now=_d(1)).emit is True


def test_zeitsprung_rueckwaerts_unterdrueckt_nicht() -> None:
    """Eine Uhr, die zurueckspringt, darf keinen Alert verschlucken."""
    deb = _mk()
    deb.decide("tradingview_webhook", "ETH", "bullish", now=_d(10))
    dec = deb.decide("tradingview_webhook", "ETH", "bullish", now=T0)
    assert dec.emit is True


def test_naive_zeitstempel_werden_nicht_gegen_aware_verglichen() -> None:
    """Gemischte Zeitzonen duerfen keinen TypeError werfen, sondern durchlassen."""
    deb = _mk()
    deb.decide("tradingview_webhook", "ETH", "bullish", now=T0)
    dec = deb.decide("tradingview_webhook", "ETH", "bullish", now=datetime(2026, 9, 15, 5, 1))
    assert dec.emit is True


# --------------------------------------------------------------------------
# Zustand
# --------------------------------------------------------------------------


def test_zustand_waechst_nicht_unbegrenzt() -> None:
    """Abgelaufene Eintraege werden geraeumt -- der Entpreller ist ein Puffer,
    kein Gedaechtnis."""
    deb = _mk()
    for i in range(500):
        deb.decide("tradingview_webhook", f"SYM{i}", "bullish", now=_d(i))
    assert deb.tracked_keys() < 500


def test_ein_neustart_laesst_durch_statt_zu_verschlucken() -> None:
    deb = _mk()
    deb.decide("tradingview_webhook", "ETH", "bullish", now=T0)
    frisch = _mk()
    assert frisch.decide("tradingview_webhook", "ETH", "bullish", now=_d(1)).emit is True


# --------------------------------------------------------------------------
# Konfiguration
# --------------------------------------------------------------------------


def test_default_fenster_ist_sechzig_minuten() -> None:
    assert DEFAULT_WINDOW_MINUTES == 60
    assert debounce_window_from_env({}) == timedelta(minutes=60)


@pytest.mark.parametrize(
    ("raw", "erwartet_minuten"),
    [
        ("15", 15),
        ("0", 0),
        ("  30  ", 30),
        ("", 60),
        ("keine-zahl", 60),
        ("-5", 0),
    ],
)
def test_fenster_aus_der_umgebung(raw: str, erwartet_minuten: int) -> None:
    env = {"KAI_ALERT_DEBOUNCE_WINDOW_MIN": raw}
    assert debounce_window_from_env(env) == timedelta(minutes=erwartet_minuten)


def test_block_reason_ist_stabil() -> None:
    """Der Grund landet in ``blocked_alerts.jsonl`` und wird ausgewertet."""
    assert DEBOUNCE_BLOCK_REASON == "repeat_within_debounce_window"


# --------------------------------------------------------------------------
# Seed aus dem Audit-Trail
# --------------------------------------------------------------------------


def test_seed_setzt_den_zustand_ohne_zu_entscheiden() -> None:
    deb = _mk()
    deb.seed("tradingview_webhook", "ETH", "bullish", emitted_at=T0)
    assert deb.decide("tradingview_webhook", "ETH", "bullish", now=_d(1)).emit is False


def test_seed_aus_audit_zeilen_nimmt_die_juengste_je_schluessel() -> None:
    rows = [
        {
            "channel": "tradingview_webhook",
            "canonical_asset": "ETH",
            "sentiment_label": "bullish",
            "dispatched_at": T0.isoformat(),
        },
        {
            "channel": "tradingview_webhook",
            "canonical_asset": "ETH",
            "sentiment_label": "bullish",
            "dispatched_at": _d(30).isoformat(),
        },
        {
            "channel": "tradingview_webhook",
            "canonical_asset": "BTC",
            "sentiment_label": "bullish",
            "dispatched_at": _d(5).isoformat(),
        },
    ]
    deb = _mk()
    seeded = seed_debouncer_from_audit_rows(deb, rows, channel="tradingview_webhook", now=_d(40))
    assert seeded == 2
    assert deb.decide("tradingview_webhook", "ETH", "bullish", now=_d(85)).emit is False
    assert deb.decide("tradingview_webhook", "ETH", "bullish", now=_d(91)).emit is True


def test_seed_ignoriert_fremde_kanaele_und_alte_zeilen() -> None:
    rows = [
        {
            "channel": "telegram",
            "canonical_asset": "ETH",
            "sentiment_label": "bullish",
            "dispatched_at": _d(59).isoformat(),
        },
        {
            "channel": "tradingview_webhook",
            "canonical_asset": "SOL",
            "sentiment_label": "bullish",
            "dispatched_at": _d(-600).isoformat(),
        },
    ]
    deb = _mk()
    assert seed_debouncer_from_audit_rows(deb, rows, channel="tradingview_webhook", now=_d(60)) == 0
    assert deb.decide("tradingview_webhook", "ETH", "bullish", now=_d(60)).emit is True
    assert deb.decide("tradingview_webhook", "SOL", "bullish", now=_d(60)).emit is True


def test_seed_faellt_auf_affected_assets_zurueck() -> None:
    """Aeltere Audit-Zeilen tragen kein ``canonical_asset`` (erst ab 01.09.)."""
    rows = [
        {
            "channel": "tradingview_webhook",
            "affected_assets": ["ETH"],
            "sentiment_label": "bullish",
            "dispatched_at": T0.isoformat(),
        },
    ]
    deb = _mk()
    assert seed_debouncer_from_audit_rows(deb, rows, channel="tradingview_webhook", now=_d(1)) == 1
    assert deb.decide("tradingview_webhook", "ETH", "bullish", now=_d(1)).emit is False


def test_seed_ueberspringt_kaputte_zeilen_ohne_zu_werfen() -> None:
    rows = [
        {
            "channel": "tradingview_webhook",
            "canonical_asset": "ETH",
            "sentiment_label": "bullish",
            "dispatched_at": "nicht-datierbar",
        },
        {"channel": "tradingview_webhook", "canonical_asset": "ETH"},
        "keine-zeile",
        {
            "channel": "tradingview_webhook",
            "canonical_asset": "BTC",
            "sentiment_label": "bullish",
            "dispatched_at": T0.isoformat(),
        },
    ]
    deb = _mk()
    assert seed_debouncer_from_audit_rows(deb, rows, channel="tradingview_webhook", now=_d(1)) == 1
    assert deb.decide("tradingview_webhook", "ETH", "bullish", now=_d(1)).emit is True
    assert deb.decide("tradingview_webhook", "BTC", "bullish", now=_d(1)).emit is False
