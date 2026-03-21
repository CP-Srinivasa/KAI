"""Signal generator — AnalysisResult + MarketDataPoint → SignalCandidate."""
from __future__ import annotations

import logging

from app.core.domain.document import AnalysisResult
from app.core.enums import SentimentLabel
from app.market_data.models import MarketDataPoint
from app.signals.models import (
    SignalCandidate,
    SignalDirection,
    _new_decision_id,
    _now_utc,
)

logger = logging.getLogger(__name__)


class SignalGenerator:
    """
    Generates a SignalCandidate from AnalysisResult + MarketDataPoint.

    Design invariants:
    - Returns None if any filter rejects (never raises)
    - All fields fully populated on success
    - Requires valid (non-stale, positive-price) market data
    - Direction derived from sentiment: bullish→LONG, bearish→SHORT, others→None
    - Confluence is computed from independent signal dimensions (max 5)
    """

    def __init__(
        self,
        *,
        min_confidence: float = 0.75,
        min_confluence: int = 2,
        stop_loss_pct: float = 2.5,    # percent below entry for LONG
        take_profit_pct: float = 5.0,  # percent above entry for LONG (2:1 R/R)
        market: str = "crypto",
        venue: str = "paper",
        mode: str = "paper",
        model_version: str = "unknown",
        prompt_version: str = "unknown",
    ) -> None:
        self._min_confidence = min_confidence
        self._min_confluence = min_confluence
        self._stop_loss_factor = stop_loss_pct / 100.0
        self._take_profit_factor = take_profit_pct / 100.0
        self._market = market
        self._venue = venue
        self._mode = mode
        self._model_version = model_version
        self._prompt_version = prompt_version

    def generate(
        self,
        analysis: AnalysisResult,
        market_data: MarketDataPoint | None,
        symbol: str,
    ) -> SignalCandidate | None:
        """
        Generate a SignalCandidate from analysis + market data.
        Returns None if any filter rejects the signal.
        """
        # Filter 1: market data required for entry price
        if market_data is None:
            logger.debug("[SIGNAL] No market data for %s — skipping", symbol)
            return None

        if market_data.price <= 0:
            logger.debug(
                "[SIGNAL] Invalid price for %s: %.4f — skipping",
                symbol, market_data.price,
            )
            return None

        # Filter 2: stale data
        if market_data.is_stale:
            logger.debug("[SIGNAL] Stale market data for %s — skipping", symbol)
            return None

        # Filter 3: confidence threshold
        if analysis.confidence_score < self._min_confidence:
            logger.debug(
                "[SIGNAL] Confidence too low for %s: %.2f < %.2f",
                symbol, analysis.confidence_score, self._min_confidence,
            )
            return None

        # Filter 4: must be actionable
        if not analysis.actionable:
            logger.debug("[SIGNAL] Analysis not actionable for %s", symbol)
            return None

        # Filter 5: direction from sentiment (neutral/mixed → no signal)
        direction = self._sentiment_to_direction(analysis.sentiment_label)
        if direction is None:
            logger.debug(
                "[SIGNAL] Neutral/mixed sentiment for %s (%s) — no signal",
                symbol, analysis.sentiment_label,
            )
            return None

        # Filter 6: confluence check
        confluence = self._calculate_confluence(analysis)
        if confluence < self._min_confluence:
            logger.debug(
                "[SIGNAL] Confluence too low for %s: %d < %d",
                symbol, confluence, self._min_confluence,
            )
            return None

        # Derive market context
        change = market_data.change_pct_24h
        market_regime = self._derive_market_regime(change)
        volatility_state = self._derive_volatility_state(change)

        # Entry / exit levels
        entry_price = market_data.price
        stop_loss_price, take_profit_price = self._calculate_levels(entry_price, direction)

        # Build narrative factors
        supporting = self._build_supporting_factors(analysis)
        contradicting = self._build_contradicting_factors(analysis)

        # Invalidation
        sl_fmt = f"{stop_loss_price:.4f}"
        invalidation = (
            f"Price closes {'below' if direction == SignalDirection.LONG else 'above'} "
            f"{sl_fmt} or analysis thesis reversed"
        )

        # Risk summary
        max_loss_pct = self._stop_loss_factor * 100
        risk_assessment = (
            f"{'Long' if direction == SignalDirection.LONG else 'Short'} entry at "
            f"{entry_price:.4f}. Stop at {stop_loss_price:.4f} "
            f"({max_loss_pct:.1f}% loss). Target at {take_profit_price:.4f}."
        )

        return SignalCandidate(
            decision_id=_new_decision_id(),
            timestamp_utc=_now_utc(),
            symbol=symbol,
            market=self._market,
            venue=self._venue,
            mode=self._mode,
            direction=direction,
            thesis=analysis.explanation_short,
            supporting_factors=tuple(supporting),
            contradictory_factors=tuple(contradicting),
            confidence_score=analysis.confidence_score,
            confluence_count=confluence,
            market_regime=market_regime,
            volatility_state=volatility_state,
            liquidity_state="adequate",
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            invalidation_condition=invalidation,
            risk_assessment=risk_assessment,
            position_size_rationale="Risk-based sizing from RiskEngine",
            max_loss_estimate_pct=max_loss_pct,
            data_sources_used=(market_data.source,),
            source_document_id=analysis.document_id,
            model_version=self._model_version,
            prompt_version=self._prompt_version,
        )

    # ─── Private helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _sentiment_to_direction(label: SentimentLabel) -> SignalDirection | None:
        if label == SentimentLabel.BULLISH:
            return SignalDirection.LONG
        if label == SentimentLabel.BEARISH:
            return SignalDirection.SHORT
        return None

    @staticmethod
    def _calculate_confluence(analysis: AnalysisResult) -> int:
        """
        Count independent confirming signal dimensions.
        Maximum 5 points:
        - High impact   (impact_score >= 0.6)
        - High relevance (relevance_score >= 0.7)
        - Novel event   (novelty_score >= 0.5)
        - Asset match   (affected_assets not empty)
        - Strong sentiment (abs(sentiment_score) >= 0.6)
        """
        score = 0
        if analysis.impact_score >= 0.6:
            score += 1
        if analysis.relevance_score >= 0.7:
            score += 1
        if analysis.novelty_score >= 0.5:
            score += 1
        if len(analysis.affected_assets) >= 1:
            score += 1
        if abs(analysis.sentiment_score) >= 0.6:
            score += 1
        return score

    @staticmethod
    def _derive_market_regime(change_pct_24h: float) -> str:
        abs_change = abs(change_pct_24h)
        if abs_change >= 5.0:
            return "volatile"
        if abs_change >= 2.0:
            return "trending"
        return "ranging"

    @staticmethod
    def _derive_volatility_state(change_pct_24h: float) -> str:
        abs_change = abs(change_pct_24h)
        if abs_change >= 8.0:
            return "extreme"
        if abs_change >= 4.0:
            return "high"
        if abs_change >= 1.0:
            return "normal"
        return "low"

    def _calculate_levels(
        self, entry_price: float, direction: SignalDirection
    ) -> tuple[float, float]:
        """Return (stop_loss_price, take_profit_price) for given direction."""
        if direction == SignalDirection.LONG:
            sl = entry_price * (1.0 - self._stop_loss_factor)
            tp = entry_price * (1.0 + self._take_profit_factor)
        else:
            sl = entry_price * (1.0 + self._stop_loss_factor)
            tp = entry_price * (1.0 - self._take_profit_factor)
        return sl, tp

    @staticmethod
    def _build_supporting_factors(analysis: AnalysisResult) -> list[str]:
        factors: list[str] = []
        if analysis.impact_score >= 0.6:
            factors.append(f"High impact score: {analysis.impact_score:.2f}")
        if analysis.relevance_score >= 0.7:
            factors.append(f"High relevance: {analysis.relevance_score:.2f}")
        if analysis.novelty_score >= 0.5:
            factors.append(f"Novel event: {analysis.novelty_score:.2f}")
        if analysis.affected_assets:
            assets = ", ".join(analysis.affected_assets[:3])
            factors.append(f"Affects: {assets}")
        if abs(analysis.sentiment_score) >= 0.6:
            factors.append(f"Strong sentiment signal: {analysis.sentiment_score:.2f}")
        return factors or ["No specific supporting factors identified"]

    @staticmethod
    def _build_contradicting_factors(analysis: AnalysisResult) -> list[str]:
        factors: list[str] = []
        if analysis.spam_probability > 0.3:
            factors.append(f"Elevated spam probability: {analysis.spam_probability:.2f}")
        if analysis.confidence_score < 0.85:
            factors.append(f"Moderate confidence: {analysis.confidence_score:.2f}")
        if not analysis.tags:
            factors.append("No context tags available")
        return factors or ["No significant contradicting factors"]
