"""Unit tests for the signal_source taxonomy resolver (2026-06-29 extraction).

resolve_signal_source is the ONE coarse source bucket every fill/close is tagged
with; reports key off it, so its precedence must be exact. The momentum_universe
branch is the regression guard: an explicit cohort tag must NOT fall through to
the autonomous_generator default (the bug that made momentum closes invisible to
extract_cohort_outcomes).
"""

from __future__ import annotations

from app.orchestrator.signal_source import (
    SOURCE_AUTONOMOUS_GENERATOR,
    SOURCE_CANARY_PROBE,
    SOURCE_REAL_ANALYSIS,
    SOURCE_TECHNICAL_PAPER,
    SOURCE_UNKNOWN,
    resolve_signal_source,
)


def _resolve(doc_id="doc-123", *, real=False, tech=False, analysis_source=None):
    return resolve_signal_source(
        doc_id,
        real_analysis_feed=real,
        technical_paper_feed=tech,
        analysis_source=analysis_source,
    )


class TestResolveSignalSource:
    def test_real_analysis_feed_wins(self) -> None:
        # real_analysis_feed beats every other input.
        assert (
            _resolve(real=True, tech=True, analysis_source="momentum_universe")
            == SOURCE_REAL_ANALYSIS
        )

    def test_technical_paper_feed_beats_analysis_source(self) -> None:
        assert _resolve(tech=True, analysis_source="momentum_universe") == SOURCE_TECHNICAL_PAPER

    def test_explicit_cohort_tag_becomes_its_own_bucket(self) -> None:
        # The regression: a momentum_universe tag must NOT degrade to
        # autonomous_generator even though the document_id is a real (non-control)
        # doc that derive() would bucket as autonomous_generator.
        assert (
            _resolve("momentum_universe_BTCUSDT", analysis_source="momentum_universe")
            == "momentum_universe"
        )

    def test_no_tag_real_generator_doc_is_autonomous(self) -> None:
        # Autonomous loop passes analysis_source=None → derive() from the doc-id.
        assert _resolve("news_doc_42", analysis_source=None) == SOURCE_AUTONOMOUS_GENERATOR

    def test_no_tag_loop_control_doc_is_canary(self) -> None:
        assert _resolve("loop_control_eth_bullish", analysis_source=None) == SOURCE_CANARY_PROBE

    def test_no_tag_empty_doc_is_unknown(self) -> None:
        assert _resolve("", analysis_source=None) == SOURCE_UNKNOWN
