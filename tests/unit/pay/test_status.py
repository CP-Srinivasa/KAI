"""Die Statusabbildung — vier Worte, drei Regeln (KAI PAY v0.1).

Sie ist rein und hat deshalb keine Ausrede: jede Uebergangsregel ist hier
direkt pruefbar, ohne Rail, ohne Datei, ohne Uhr aus dem Modul.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.pay.status import TERMINAL, PayStatus, RailView, next_status

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
LATER = NOW + timedelta(minutes=15)
EARLIER = NOW - timedelta(minutes=1)


def test_ein_bezahltes_invoice_wird_settled() -> None:
    assert (
        next_status(PayStatus.WAITING, RailView(settled=True), now=NOW, expires_at=LATER)
        is PayStatus.SETTLED
    )


def test_settled_bleibt_settled_auch_nach_ablauf() -> None:
    """Final heisst final. Eine Leistung zweimal zu liefern ist teurer als Warten."""
    assert (
        next_status(PayStatus.SETTLED, RailView(settled=False), now=NOW, expires_at=EARLIER)
        is PayStatus.SETTLED
    )


def test_unbezahlt_und_frist_um_ist_expired() -> None:
    assert (
        next_status(PayStatus.WAITING, RailView(settled=False), now=NOW, expires_at=EARLIER)
        is PayStatus.EXPIRED
    )


def test_unbezahlt_und_frist_offen_bleibt_waiting() -> None:
    assert (
        next_status(PayStatus.WAITING, RailView(settled=False), now=NOW, expires_at=LATER)
        is PayStatus.WAITING
    )


def test_ohne_rail_aussage_laeuft_nichts_ab() -> None:
    """Fail-soft: ``EXPIRED`` ist eine Behauptung ueber Geld, nicht ueber die Uhr.

    Der Rail schweigt (Timeout, Node weg). Waere die Uhr allein massgeblich,
    wuerde ein Netzausfall jede offene Forderung fuer unbezahlt erklaeren —
    und eine kurz zuvor eingegangene Zahlung mit ihr.
    """
    assert (
        next_status(PayStatus.WAITING, RailView(settled=None), now=NOW, expires_at=EARLIER)
        is PayStatus.WAITING
    )


def test_eine_endgueltige_absage_wird_failed() -> None:
    assert (
        next_status(
            PayStatus.WAITING, RailView(settled=False, canceled=True), now=NOW, expires_at=LATER
        )
        is PayStatus.FAILED
    )


def test_terminal_ist_erschoepfend() -> None:
    """Genau die drei Endzustaende — ``WAITING`` gehoert nie dazu."""
    assert TERMINAL == {PayStatus.SETTLED, PayStatus.EXPIRED, PayStatus.FAILED}
    assert PayStatus.WAITING not in TERMINAL


def test_aus_einem_endzustand_fuehrt_kein_weg_zurueck() -> None:
    for terminal in TERMINAL:
        assert next_status(terminal, RailView(settled=True), now=NOW, expires_at=LATER) is terminal
