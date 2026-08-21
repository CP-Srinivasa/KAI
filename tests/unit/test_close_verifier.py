"""Der Offline-Verifier: zwei Evidenzstärken, die nie verwischen dürfen.

Kern der Prüfung ist nicht, dass „verified" herauskommt, sondern dass ein
fehlendes Pflichtstück zu `UNVERIFIED` **mit benanntem Grund** führt — nie zu
„wahrscheinlich echt".
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.execution.close_evidence import CloseEvidence, VenueCandle
from app.execution.close_verifier import (
    MAX_QUOTE_AGE_MS,
    ProvenanceClass,
    ReasonCode,
    VerifierVerdict,
    verifier_code_sha,
    verify_close,
)

MINUTE = 60_000
# 2026-08-21T09:00:00+00:00
BASE_MS = 1_787_302_800_000
CLOSE_TS = "2026-08-21T09:00:30+00:00"
SLIP = 0.0005
REFERENCE = 100.0
EXIT = REFERENCE * (1 - SLIP)


def _evidence(**kw) -> CloseEvidence:
    defaults = {
        "close_fill_id": "fill_abc",
        "close_order_id": "ord_abc",
        "symbol": "ETH/USDT",
        "close_timestamp_utc": CLOSE_TS,
        "venue": "bybit",
        "interval": "1m",
        "window_start_ms": BASE_MS,
        "window_end_ms": BASE_MS + 5 * MINUTE,
        "candles": (VenueCandle(BASE_MS, 99.5, 101.0, 99.0, 100.2),),
        "collected_at_utc": "2026-08-21T09:05:00+00:00",
        "collector_code_sha": "c0ffee",
    }
    defaults.update(kw)
    return CloseEvidence(**defaults)


def _full_close(**kw) -> dict[str, object]:
    row = {
        "event_type": "position_closed",
        "symbol": "ETH/USDT",
        "timestamp_utc": CLOSE_TS,
        "fill_id": "fill_abc",
        "order_id": "ord_abc",
        "position_side": "long",
        "exit_price": EXIT,
        "price_source": "bybit",
        "price_observed_at_utc": "2026-08-21T09:00:28+00:00",
        "observed_market_price": REFERENCE,
        "execution_reference_price": REFERENCE,
        "market_data_is_stale": False,
        "market_data_age_ms": 2000.0,
        "monitor_tick_id": "tick_" + "a" * 32,
    }
    row.update(kw)
    return row


def _legacy_close(**kw) -> dict[str, object]:
    row = {
        "event_type": "position_closed",
        "symbol": "ETH/USDT",
        "timestamp_utc": CLOSE_TS,
        "fill_id": "fill_abc",
        "order_id": "ord_abc",
        "position_side": "long",
        "exit_price": EXIT,
    }
    row.update(kw)
    return row


# --- Vollständige Kette ---------------------------------------------------------


def test_vollstaendige_kette_ergibt_execution_provenance() -> None:
    ev = _evidence()
    r = verify_close(_full_close(), ev, expected_evidence_sha256=ev.payload_sha256())
    assert r.verdict is VerifierVerdict.VERIFIED_EXECUTION_PROVENANCE
    assert r.provenance_class is ProvenanceClass.FULL
    assert r.reasons == ()
    assert len(r.evidence_sha256) == 64
    assert len(r.verifier_code_sha) == 64


@pytest.mark.parametrize(
    ("feld", "wert", "code"),
    [
        ("price_source", "", ReasonCode.MISSING_PRICE_SOURCE),
        ("price_observed_at_utc", "", ReasonCode.MISSING_OBSERVED_TIMESTAMP),
        ("observed_market_price", None, ReasonCode.MISSING_OBSERVED_MARKET_PRICE),
        ("execution_reference_price", None, ReasonCode.MISSING_EXECUTION_REFERENCE_PRICE),
        ("monitor_tick_id", "", ReasonCode.MISSING_TICK_ID),
        ("market_data_age_ms", None, ReasonCode.AGE_UNAVAILABLE),
    ],
)
def test_jedes_fehlende_pflichtfeld_nennt_seinen_grund(
    feld: str, wert: object, code: ReasonCode
) -> None:
    """Damit niemand aus 37x UNVERIFIED schliesst, die seien halt alt gewesen."""
    r = verify_close(_full_close(**{feld: wert}), _evidence())
    assert r.verdict is VerifierVerdict.UNVERIFIED
    assert code in r.reasons


def test_stale_true_ist_negative_evidenz() -> None:
    r = verify_close(_full_close(market_data_is_stale=True), _evidence())
    assert r.verdict is VerifierVerdict.UNVERIFIED
    assert ReasonCode.STALE_MARKET_DATA in r.reasons


def test_stale_none_ist_fehlende_evidenz_mit_eigenem_grund() -> None:
    """None und True verhindern beide den PASS — aus VERSCHIEDENEN Gruenden."""
    r = verify_close(_full_close(market_data_is_stale=None), _evidence())
    assert r.verdict is VerifierVerdict.UNVERIFIED
    assert ReasonCode.STALE_FLAG_UNKNOWN in r.reasons
    assert ReasonCode.STALE_MARKET_DATA not in r.reasons


def test_zu_alte_quote_faellt_durch() -> None:
    r = verify_close(_full_close(market_data_age_ms=MAX_QUOTE_AGE_MS + 1), _evidence())
    assert ReasonCode.AGE_EXCEEDS_LIMIT in r.reasons


def test_synthetische_quelle_ist_quarantaene_nicht_unverified() -> None:
    """Mock ist kein Anbieter — das ist ein Befund, kein Evidenzmangel."""
    r = verify_close(_full_close(price_source="mock|synthetic_not_tradeable"), _evidence())
    assert r.verdict is VerifierVerdict.QUARANTINE
    assert ReasonCode.SYNTHETIC_PRICE_SOURCE in r.reasons


def test_slippage_muss_intern_bit_exakt_aufgehen() -> None:
    r = verify_close(_full_close(exit_price=EXIT + 1e-9), _evidence())
    assert ReasonCode.SLIPPAGE_MISMATCH in r.reasons


def test_short_close_nutzt_die_andere_slippage_richtung() -> None:
    ref = 100.0
    row = _full_close(
        position_side="short",
        execution_reference_price=ref,
        exit_price=ref * (1 + SLIP),
        observed_market_price=ref,
    )
    ev = _evidence(candles=(VenueCandle(BASE_MS, 99.5, 101.0, 99.0, 100.2),))
    r = verify_close(row, ev, expected_evidence_sha256=ev.payload_sha256())
    assert ReasonCode.SLIPPAGE_MISMATCH not in r.reasons


# --- Legacy: Höchststatus ist Marktplausibilität --------------------------------


def test_legacy_erreicht_hoechstens_market_plausible() -> None:
    ev = _evidence()
    r = verify_close(_legacy_close(), ev, expected_evidence_sha256=ev.payload_sha256())
    assert r.verdict is VerifierVerdict.VERIFIED_MARKET_PLAUSIBLE
    assert r.provenance_class is ProvenanceClass.UNAVAILABLE_BY_LEGACY_SCHEMA


def test_legacy_wird_niemals_execution_provenance() -> None:
    """Keine noch so gute Kerzen-Rekonstruktion macht daraus Provenienz."""
    ev = _evidence()
    for _ in range(3):
        r = verify_close(_legacy_close(), ev, expected_evidence_sha256=ev.payload_sha256())
        assert r.verdict is not VerifierVerdict.VERIFIED_EXECUTION_PROVENANCE


def test_legacy_ohne_evidenz_bleibt_unverified() -> None:
    r = verify_close(_legacy_close(), None)
    assert r.verdict is VerifierVerdict.UNVERIFIED
    assert ReasonCode.VENUE_WINDOW_UNAVAILABLE in r.reasons


def test_legacy_ausserhalb_des_bandes_bleibt_unverified() -> None:
    r = verify_close(_legacy_close(exit_price=500.0), _evidence())
    assert r.verdict is VerifierVerdict.UNVERIFIED
    assert ReasonCode.OBSERVED_PRICE_OUTSIDE_VENUE_BAND in r.reasons


# --- Evidenz-Artefakt ------------------------------------------------------------


def test_verfaelschtes_artefakt_wird_erkannt() -> None:
    ev = _evidence()
    r = verify_close(_full_close(), ev, expected_evidence_sha256="0" * 64)
    assert ReasonCode.EVIDENCE_HASH_MISMATCH in r.reasons


def test_artefakt_fuer_ein_anderes_symbol_zaehlt_nicht() -> None:
    r = verify_close(_full_close(), _evidence(symbol="BTC/USDT"))
    assert ReasonCode.EVIDENCE_SYMBOL_MISMATCH in r.reasons


def test_fenster_deckt_den_close_nicht_ab() -> None:
    r = verify_close(
        _full_close(),
        _evidence(candles=(VenueCandle(BASE_MS + 10 * MINUTE, 99.5, 101.0, 99.0, 100.2),)),
    )
    assert ReasonCode.VENUE_WINDOW_DOES_NOT_COVER_CLOSE in r.reasons


def test_identitaet_muss_zum_artefakt_passen() -> None:
    r = verify_close(_full_close(), _evidence(close_fill_id="fill_fremd"))
    assert ReasonCode.IDENTITY_CHAIN_MISMATCH in r.reasons


def test_close_ohne_identitaet_wird_nie_verifiziert() -> None:
    r = verify_close(_full_close(fill_id=""), _evidence())
    assert r.verdict is VerifierVerdict.UNVERIFIED
    assert ReasonCode.MISSING_CLOSE_IDENTITY in r.reasons


# --- Reinheit: derselbe Input, dasselbe Urteil ----------------------------------


def test_urteil_ist_deterministisch() -> None:
    row, ev = _full_close(), _evidence()
    first = verify_close(row, ev)
    second = verify_close(row, ev)
    assert first == second


def test_verifier_modul_kann_kein_netz() -> None:
    """Nicht 'wir rufen normalerweise keine API' — architektonisch unmoeglich.

    Der Verifier urteilt ueber ein persistiertes Artefakt. Duerfte er selbst
    nachladen, haenge sein Urteil an der API-Verfuegbarkeit des Moments statt an
    der Evidenz.
    """
    source = (
        Path(__file__).resolve().parents[2] / "app" / "execution" / "close_verifier.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
            if node.module.startswith("app."):
                imported.add(node.module)

    verboten = {"httpx", "requests", "aiohttp", "urllib", "urllib3", "socket", "websockets"}
    assert not (imported & verboten), f"Netz-Import im Verifier: {imported & verboten}"
    adapter = {m for m in imported if "market_data" in m or "adapter" in m}
    assert not adapter, f"Venue-Adapter im Verifier: {adapter}"


def test_code_sha_aendert_sich_mit_den_regeln() -> None:
    """An jedem Urteil haengt, WELCHE Regelversion es gefaellt hat."""
    sha = verifier_code_sha()
    assert len(sha) == 64
    assert verify_close(_full_close(), _evidence()).verifier_code_sha == sha


# --- Der richtige Preis gegen den Markt (die Trennung aus #746) ----------------


def test_full_prueft_den_beobachteten_preis_gegen_die_kerze() -> None:
    """Bei einer Liquidation ist der exit_price NICHT der Marktpreis.

    Der Verifier hielt zuvor fuer beide Klassen den ``exit_price`` gegen das
    Kerzenband — und haette damit bei jeder Liquidation entweder eine Abweichung
    gemeldet, die es nicht gibt, oder eine echte verdeckt.
    """
    observed = 100.0  # liegt in der Kerze 99.0..101.0
    liq = 92.0  # ausserhalb — die Engine fuellte gegen den Liquidationspreis
    row = _full_close(
        observed_market_price=observed,
        execution_reference_price=liq,
        exit_price=liq * (1 - SLIP),
        reason="liquidation",
    )
    ev = _evidence()
    r = verify_close(row, ev, expected_evidence_sha256=ev.payload_sha256())
    assert ReasonCode.OBSERVED_PRICE_OUTSIDE_VENUE_BAND not in r.reasons
    assert r.verdict is VerifierVerdict.VERIFIED_EXECUTION_PROVENANCE


def test_full_meldet_wenn_der_beobachtete_preis_ausserhalb_liegt() -> None:
    row = _full_close(observed_market_price=500.0)
    ev = _evidence()
    r = verify_close(row, ev, expected_evidence_sha256=ev.payload_sha256())
    assert ReasonCode.OBSERVED_PRICE_OUTSIDE_VENUE_BAND in r.reasons


def test_legacy_haelt_weiterhin_den_exit_preis_gegen_die_kerze() -> None:
    """Ohne Provenienz gibt es nichts anderes — deshalb ist dort auch Schluss."""
    ev = _evidence()
    r = verify_close(
        _legacy_close(exit_price=500.0), ev, expected_evidence_sha256=ev.payload_sha256()
    )
    assert ReasonCode.OBSERVED_PRICE_OUTSIDE_VENUE_BAND in r.reasons


# --- Verankerung ----------------------------------------------------------------


def test_ohne_verankerten_hash_kein_verified() -> None:
    """Sonst prueft der Verifier gegen den Hash, den er selbst gerade ausrechnet."""
    r = verify_close(_full_close(), _evidence())
    assert r.verdict is VerifierVerdict.UNVERIFIED
    assert ReasonCode.MISSING_EVIDENCE_HASH in r.reasons


def test_ohne_verankerten_hash_auch_kein_legacy_verified() -> None:
    r = verify_close(_legacy_close(), _evidence())
    assert r.verdict is VerifierVerdict.UNVERIFIED
    assert ReasonCode.MISSING_EVIDENCE_HASH in r.reasons


# --- Vollstaendige Identitaetskette ---------------------------------------------


def test_artefakt_einer_fremden_order_zaehlt_nicht() -> None:
    ev = _evidence(close_order_id="ord_fremd")
    r = verify_close(_full_close(), ev, expected_evidence_sha256=ev.payload_sha256())
    assert ReasonCode.EVIDENCE_ORDER_MISMATCH in r.reasons


def test_artefakt_einer_fremden_close_zeit_zaehlt_nicht() -> None:
    ev = _evidence(close_timestamp_utc="2026-08-21T10:00:00+00:00")
    r = verify_close(_full_close(), ev, expected_evidence_sha256=ev.payload_sha256())
    assert ReasonCode.EVIDENCE_CLOSE_TIME_MISMATCH in r.reasons


def test_venue_muss_zur_preisquelle_passen() -> None:
    ev = _evidence(venue="binance")
    r = verify_close(
        _full_close(price_source="bybit"), ev, expected_evidence_sha256=ev.payload_sha256()
    )
    assert ReasonCode.VENUE_SOURCE_MISMATCH in r.reasons


def test_marker_an_der_quelle_stoert_den_venue_abgleich_nicht() -> None:
    """`bybit|provider_disagreement:…` ist weiterhin bybit."""
    ev = _evidence(venue="bybit")
    row = _full_close(price_source="bybit|provider_disagreement:1vs2")
    r = verify_close(row, ev, expected_evidence_sha256=ev.payload_sha256())
    assert ReasonCode.VENUE_SOURCE_MISMATCH not in r.reasons


def test_fehlende_close_zeit_hat_einen_eigenen_code() -> None:
    """Nicht mit einer fehlenden QUOTE-Zeit verwechseln."""
    ev = _evidence()
    r = verify_close(
        _full_close(timestamp_utc=""), ev, expected_evidence_sha256=ev.payload_sha256()
    )
    assert ReasonCode.MISSING_CLOSE_TIMESTAMP in r.reasons
