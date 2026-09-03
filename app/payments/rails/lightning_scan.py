"""Seitenweises Lesen von ``ListPayments`` (ADR 0017 §8).

Beide Reconciliation-Richtungen brauchen dieselbe Schleife: die Vorwaerts-Frage
``kennt der Node DIESEN payment_hash?`` und die Rueckwaerts-Frage ``welche Sends
kennt der Node ueberhaupt?``. Sie stand zweimal in
:mod:`app.payments.rails.lightning` — mit zwei Abbruchbedingungen, die
auseinanderlaufen konnten, und mit dem Modul ueber der 350-Zeilen-Grenze.

**Die Grenze ist Teil der Zusage.** Eine unbegrenzte Schleife auf dem Geldpfad
ist ein Haenger, kein Ergebnis. Wird sie erreicht, sagt
:attr:`ScanResult.complete` ``False`` — der Aufrufer darf daraus dann kein
"nichts gefunden" machen.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

#: Seitengroesse und Sicherheitsgrenze (Muster ``reconciliation._scan_all_payments``).
PAGE_SIZE = 500
MAX_PAGES = 40


@dataclass(frozen=True)
class ScanResult:
    """Was die Seitenschleife gesehen hat — und ob sie fertig wurde."""

    rows: tuple[Any, ...]
    complete: bool


async def scan_payments(
    client: Any,
    *,
    include_incomplete: bool,
    keep: Callable[[Any], bool] = lambda _row: True,
    stop_after_first_hit: bool = False,
) -> ScanResult:
    """Laufe ``ListPayments`` vorwaerts durch und sammle, was ``keep`` behaelt.

    ``stop_after_first_hit`` bricht ab, sobald der erste Treffer da ist — die
    Vorwaerts-Frage sucht genau einen ``payment_hash`` und hat danach nichts
    mehr zu holen.
    """
    rows: list[Any] = []
    offset = 0
    for _ in range(MAX_PAGES):
        page = await client.list_payments(
            include_incomplete=include_incomplete,
            index_offset=offset,
            max_payments=PAGE_SIZE,
            reversed=False,
            omit_hops=True,
        )
        for row in _iter_payments(page):
            if not keep(row):
                continue
            rows.append(row)
            if stop_after_first_hit:
                return ScanResult(rows=tuple(rows), complete=True)
        if len(page.payments) < PAGE_SIZE:
            return ScanResult(rows=tuple(rows), complete=True)
        next_offset = page.next_index_offset
        if next_offset <= offset:
            return ScanResult(rows=tuple(rows), complete=True)
        offset = next_offset
    return ScanResult(rows=tuple(rows), complete=False)


def _iter_payments(page: Any) -> Iterator[Any]:
    yield from page.payments


__all__ = ["MAX_PAGES", "PAGE_SIZE", "ScanResult", "scan_payments"]
