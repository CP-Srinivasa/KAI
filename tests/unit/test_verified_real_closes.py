"""Ein Freispruch darf niemals auf einen anderen Trade überspringen.

Die erste Fassung erkannte einen freigesprochenen Close an ``symbol`` +
``exit_price``. Dasselbe Symbol kann Monate später wieder ungefähr denselben
Exit-Preis haben — dann hätte ein vollkommen anderer Close den historischen
Freispruch geerbt. Identifiziert wird deshalb über die Ereignis-ID; alles
andere ist Integritätsprüfung.
"""

from __future__ import annotations

import hashlib

import pytest

from app.learning.bayes_quarantine import corruption_reason
from app.learning.verified_real_closes import (
    VERIFIED_REAL_CLOSES,
    is_verified_real_close,
    verified_real_close,
)

CYS = {
    "event_type": "position_closed",
    "symbol": "CYS/USDT",
    "timestamp_utc": "2026-08-11T09:28:30.842264+00:00",
    "fill_id": "fill_fbd5580fab5c",
    "order_id": "ord_986917c2f200",
    "entry_price": 1.0011002999999998,
    "exit_price": 1.3897048,
    "position_side": "long",
}


def _row(**overrides: object) -> dict[str, object]:
    row = dict(CYS)
    row.update(overrides)
    return row


# --- der Freispruch selbst ------------------------------------------------------


def test_bekannter_close_wird_freigesprochen() -> None:
    rec = verified_real_close(_row())
    assert rec is not None
    assert rec.symbol == "CYS/USDT"
    assert corruption_reason(_row()) is None


@pytest.mark.parametrize("rec", VERIFIED_REAL_CLOSES, ids=lambda r: r.symbol)
def test_jeder_eintrag_traegt_eine_ereignis_id(rec) -> None:
    assert rec.fill_id.startswith("fill_")
    assert rec.order_id.startswith("ord_")
    assert rec.timestamp_utc.endswith("+00:00")
    assert rec.exit_price > 0


# --- Identität statt Ähnlichkeit ------------------------------------------------


def test_gleicher_preis_andere_id_erbt_den_freispruch_nicht() -> None:
    """Der Kern: ein künftiger Trade mit identischem Exit-Preis bleibt außen vor."""
    fremd = _row(
        fill_id="fill_ffffffffffff",
        order_id="ord_ffffffffffff",
        timestamp_utc="2027-01-05T10:00:00.000000+00:00",
    )
    assert verified_real_close(fremd) is None
    assert not is_verified_real_close(fremd)


def test_close_ohne_fill_id_wird_nie_freigesprochen() -> None:
    assert verified_real_close(_row(fill_id="")) is None
    row = _row()
    del row["fill_id"]
    assert verified_real_close(row) is None


@pytest.mark.parametrize(
    ("feld", "wert"),
    [
        ("symbol", "ETH/USDT"),
        ("order_id", "ord_000000000000"),
        ("timestamp_utc", "2026-08-11T09:28:31.000000+00:00"),
        ("exit_price", 1.3897050),
    ],
)
def test_identitaet_trifft_aber_integritaet_bricht_kein_freispruch(feld: str, wert: object) -> None:
    """fill_id stimmt, etwas anderes nicht — fail-closed, kein Freispruch."""
    assert verified_real_close(_row(**{feld: wert})) is None


def test_unbrauchbarer_exit_price_bricht_den_freispruch() -> None:
    for kaputt in (None, "1.39", float("nan")):
        assert verified_real_close(_row(exit_price=kaputt)) is None


# --- Evidenz ---------------------------------------------------------------------


@pytest.mark.parametrize("rec", VERIFIED_REAL_CLOSES, ids=lambda r: r.symbol)
def test_evidence_hash_matches_text(rec) -> None:
    """Wer den Beleg umschreibt, ohne den Hash mitzuführen, bricht diesen Test."""
    assert rec.evidence_hash_ok()
    assert hashlib.sha256(rec.evidence.encode("utf-8")).hexdigest() == rec.evidence_sha256


@pytest.mark.parametrize("rec", VERIFIED_REAL_CLOSES, ids=lambda r: r.symbol)
def test_evidence_nennt_das_kerzenband(rec) -> None:
    """Ein Beleg ohne prüfbare Zahlen ist kein Beleg."""
    assert "Kerze" in rec.evidence
    assert "low" in rec.evidence and "high" in rec.evidence
    assert "Roh-Preis" in rec.evidence


# --- Rangfolge -------------------------------------------------------------------


def test_freispruch_ueberstimmt_keine_exakte_signatur() -> None:
    """Ein Freispruch schlägt nur die Heuristik, nie einen benannten Vorfall."""
    row = {
        "event_type": "position_closed",
        "symbol": "ETH/USDT",
        "fill_id": "fill_fbd5580fab5c",  # sogar eine freigesprochene ID
        "order_id": "ord_986917c2f200",
        "entry_price": 1874.24956227636,
        "exit_price": 3225.6863500000004,  # aber ein Mock-Preis
        "position_side": "long",
    }
    assert corruption_reason(row) == "mock_synthetic_exit_price"
