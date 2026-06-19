"""Tests for the bearish-short gate on the news/real-analysis paper path (IC-Hebel).

Edge basis (2026-06-17, shadow_candidate_resolved): news longs carry (+36bps
signed @3600s) while shorts are ~breakeven/negative → suppressing shorts
concentrates the measured generator cohort on the long edge. Default-open
(allow_short_news=True) so a fresh deploy is inert (measure-first).
"""

from __future__ import annotations

from typing import cast

from app.core.domain.document import AnalysisResult
from app.core.settings import AlertSettings
from app.observability.real_analysis_paper_feeder import _apply_bearish_short_gate
from app.observability.real_analysis_paper_selector import RealAnalysisCandidate


def _cand(direction: str) -> RealAnalysisCandidate:
    # The gate only reads ``.direction``; ``analysis`` is irrelevant here.
    return RealAnalysisCandidate(
        document_id="doc",
        symbol="BTC/USDT",
        direction=direction,
        analysis=cast(AnalysisResult, object()),
    )


def test_gate_open_keeps_shorts() -> None:
    cands = [_cand("long"), _cand("short")]
    kept, suppressed = _apply_bearish_short_gate(cands, allow_short_news=True)
    assert kept == cands  # unchanged identity → deploy inert
    assert suppressed == 0


def test_gate_closed_drops_shorts_keeps_longs() -> None:
    kept, suppressed = _apply_bearish_short_gate(
        [_cand("long"), _cand("short"), _cand("short"), _cand("long")],
        allow_short_news=False,
    )
    assert [c.direction for c in kept] == ["long", "long"]
    assert suppressed == 2


def test_gate_closed_no_shorts_is_noop() -> None:
    kept, suppressed = _apply_bearish_short_gate(
        [_cand("long"), _cand("long")], allow_short_news=False
    )
    assert len(kept) == 2
    assert suppressed == 0


def test_gate_closed_all_shorts_empties() -> None:
    kept, suppressed = _apply_bearish_short_gate([_cand("short")], allow_short_news=False)
    assert kept == []
    assert suppressed == 1


def test_setting_defaults_open_measure_first() -> None:
    # Default TRUE = status quo on deploy (shorts still flow); operator closes it.
    assert AlertSettings(_env_file=None).allow_short_news is True
