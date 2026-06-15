"""WP-C (regime-edge-capture 2026-06-15): paper_maker Kosten-Venue.

Befund (Edge-Attribution): der broad Richtungs-Edge (~+12 bps brutto) trägt die
~20-bps-Taker-Round-Trip nicht, eine ~4-bps-Maker-Round-Trip aber schon. Die
`paper_maker`-Venue erlaubt die EV-Messung unter Maker-Kosten
(`edge-report --venue paper_maker`) — KEIN Default, kein Live-Eingriff.
"""

from __future__ import annotations

from app.execution.cost_model import CostModel


def test_paper_maker_roundtrip_is_maker_tier() -> None:
    cm = CostModel()
    rt = cm.round_trip_fee_pct(venue="paper_maker")
    # 2 bps/Seite ⇒ ~0.04% Round-Trip (Default-Side=taker, daher taker=2bps gesetzt).
    assert rt == 0.04


def test_paper_maker_cheaper_than_paper_taker() -> None:
    cm = CostModel()
    assert cm.round_trip_fee_pct(venue="paper_maker") < cm.round_trip_fee_pct(venue="paper")


def test_paper_default_unchanged() -> None:
    # Regression: die Default-paper-Venue bleibt bei 10 bp/Seite (0.20% RT).
    assert CostModel().round_trip_fee_pct(venue="paper") == 0.20
