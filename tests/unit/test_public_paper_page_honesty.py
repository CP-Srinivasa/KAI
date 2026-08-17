"""Die öffentliche ``/paper``-Seite darf kein offenes Kriterium als bestanden zeigen.

Befund 2026-08-17: Der Block zur pra-registrierten K1-Resonanz
(``00c75a76a2b0e78b``) trug ``class="verdict pass"`` samt grünem
``chip pass`` — die Farbe von "bestanden". Tatsächlich war der Claim am
2026-07-04 mit 30-Tage-Horizont registriert, sein Fenster schloss am
2026-08-03, und ein Verdikt existiert bis heute **nicht**
(``prereg_verdicts.jsonl`` kennt die ID nicht).

Eine Seite, deren gesamte These "wir versiegeln das Urteil, wie immer es
ausfiel" lautet, darf ein unbewertetes Kriterium nicht grün darstellen. Das
ist kein Schönheitsfehler, sondern derselbe Fehler, den die Methodik nach
außen anprangert — nur auf der eigenen Seite.

Die Frist wurde zudem von keinem Timer überwacht: der Claim taucht in
``prereg-maturity`` gar nicht auf. Ein Verdikt kann deshalb nur der Operator
fällen (die Zählung "qualifizierter Anfragen" liegt in seinem Posteingang) —
die Seite muss bis dahin ehrlich bleiben.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_PAGE = Path(__file__).resolve().parents[2] / "app" / "api" / "static" / "paper.html"
# Verdikt-Blöcke, die als "bestanden" eingefärbt werden dürfen, brauchen ein
# gesiegeltes Verdikt. Diese ID hat keines.
_UNSEALED_PREREG = "00c75a76a2b0e78b"


@pytest.fixture(scope="module")
def page() -> str:
    return _PAGE.read_text(encoding="utf-8")


def test_the_page_exists_and_is_not_empty(page: str) -> None:
    """Guard: eine leere Datei würde alle folgenden Tests trivial grün färben."""
    assert len(page) > 1000


def _block_containing(page: str, needle: str) -> str:
    """Der ``<div class="verdict …">``-Block, der ``needle`` enthält."""
    blocks = re.findall(r'<div class="verdict[^"]*">.*?</div>', page, re.S)
    matching = [b for b in blocks if needle in b]
    assert matching, f"Kein verdict-Block enthaelt {needle!r}"
    return matching[0]


def test_the_unsealed_k1_criterion_is_not_rendered_as_passed(page: str) -> None:
    block = _block_containing(page, _UNSEALED_PREREG)

    assert 'class="verdict pass"' not in block, (
        "Das K1-Resonanz-Kriterium ist nicht gesiegelt (kein Eintrag in "
        "prereg_verdicts.jsonl) und darf nicht als bestanden eingefaerbt werden."
    )
    assert "chip pass" not in block, "Gruener PASS-Chip auf einem Claim ohne Verdikt."


def test_the_closed_window_is_stated_on_the_page(page: str) -> None:
    """Ein verstrichenes Fenster muss der Leser sehen, nicht nur der Operator."""
    block = _block_containing(page, _UNSEALED_PREREG)

    assert "2026-08-03" in block, "Das Fensterende des Claims fehlt auf der Seite."
    lowered = block.lower()
    assert "outstanding" in lowered or "pending" in lowered, (
        "Der Block sagt nicht, dass das Verdikt noch aussteht."
    )


def test_no_verdict_block_claims_pass_without_a_sealed_marker(page: str) -> None:
    """Ein gruener Block muss eine Siegel-Angabe tragen ('sealed' oder seq)."""
    for block in re.findall(r'<div class="verdict pass">.*?</div>', page, re.S):
        lowered = block.lower()
        assert "sealed" in lowered or re.search(r"seq\s*\d+", lowered), (
            f"PASS-Block ohne Siegel-Nachweis: {block[:200]}"
        )
