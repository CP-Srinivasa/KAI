"""Die oeffentliche /paper-Seite darf keinen ueberholten K1-Status behaupten.

Befund 2026-08-31: nach der Attestierung von K1 (Truth-seq 114,
INCONCLUSIVE_BY_TIMEOUT) sagte die Seite an drei Stellen weiter „verdict
outstanding" bzw. „not yet sealed" — und zwar ausgerechnet in dem Absatz, der
verspricht: „this page will not imply otherwise". Eine oeffentliche Seite, die
ihren eigenen Ehrlichkeitsanspruch ueberlebt, ist die teuerste Sorte falscher
Aussage: sie richtet sich an Dritte.

Der Test pinnt die terminalen Fakten. Er ist bewusst grob — er prueft nicht die
Formulierung, sondern dass die WIDERRUFENEN Behauptungen nicht zurueckkehren und
die terminale Klasse genannt wird.
"""

from __future__ import annotations

from pathlib import Path

import pytest

PAGE = Path(__file__).resolve().parents[2] / "app" / "api" / "static" / "paper.html"


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


@pytest.mark.parametrize("widerrufen", ["verdict outstanding", "not yet sealed"])
def test_die_widerrufenen_behauptungen_kehren_nicht_zurueck(page: str, widerrufen: str) -> None:
    assert widerrufen not in page, (
        f"K1 ist seit 2026-08-31 terminal geschlossen (Truth-seq 114) — "
        f"{widerrufen!r} waere eine oeffentliche Falschaussage."
    )


def test_die_seite_nennt_die_terminale_klasse(page: str) -> None:
    assert "INCONCLUSIVE_BY_TIMEOUT" in page
    assert "substantive_verdict = NONE" in page
    assert "seq 114" in page


def test_die_seite_behauptet_kein_sachverdikt(page: str) -> None:
    """Weder bestanden noch gescheitert — genau das ist die Aussage."""
    block = page.split("Verifiability")[1]
    assert "neither" in block and "nor" in block
    # Und ausdruecklich keine Unmessbarkeits-Behauptung.
    assert "impossible to measure" in block


def test_die_seite_behauptet_keine_kausalkette(page: str) -> None:
    """Die Operator-Policy ist Kontext, nicht Ursache des fehlenden Verdikts."""
    block = page.split("Verifiability")[1]
    assert "Context, not cause" in block


def test_der_juli_kettenstand_ist_als_schnappschuss_gekennzeichnet(page: str) -> None:
    """`seq 30 :: records=30` war der Stand bei Veroeffentlichung, nicht heute.

    Still auf 114 zu heben waere falsch gewesen: die canonical-edge-Payload in
    derselben Zeile stammt aus demselben Juli-Stand und waere damit neu und
    unzutreffend datiert.
    """
    assert "truth-ledger snapshot 2026-07" in page
