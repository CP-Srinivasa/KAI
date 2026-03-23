"""Signal generator — AnalysisResult + MarketDataPoint → SignalCandidate.

SIGNAL-KERN: Richtung aus LLM-Sentiment (bullish→LONG, bearish→SHORT).
Confluence-Score kombiniert Analyse-Metadaten (impact, relevance, novelty, assets,
sentiment strength) MIT Marktdaten (price-momentum, volume-confirmation).

Marktdaten-Dimensionen (wenn CoinGecko-Daten vorhanden):
- price_momentum: change_pct_24h in Richtung des Signals (>= threshold)
- volume_confirmation: volume_24h >= volume_threshold

TODO (vor Live-Einsatz): ATR-basierter SL/TP, Orderbook-Input.
"""
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

    # Thresholds for market-data confluence dimensions
    _PRICE_MOMENTUM_THRESHOLD_PCT: float = 2.0   # |change_24h| >= 2% counts as momentum
    _VOLUME_THRESHOLD_USD: float = 1_000_000.0   # volume_24h >= $1M counts as confirmed

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
        price_momentum_threshold_pct: float | None = None,
        volume_threshold_usd: float | None = None,
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
        self._price_momentum_threshold_pct = (
            price_momentum_threshold_pct
            if price_momentum_threshold_pct is not None
            else self._PRICE_MOMENTUM_THRESHOLD_PCT
        )
        self._volume_threshold_usd = (
            volume_threshold_usd
            if volume_threshold_usd is not None
            else self._VOLUME_THRESHOLD_USD
        )

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

        # Filter 6: confluence check (includes market-data dimensions)
        confluence = self._calculate_confluence(analysis, market_data, direction)
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
        supporting = self._build_supporting_factors(analysis, market_data, direction)
        contradicting = self._build_contradicting_factors(analysis, market_data, direction)

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

    def _calculate_confluence(
        self,
        analysis: AnalysisResult,
        market_data: MarketDataPoint,
        direction: SignalDirection,
    ) -> int:
        """
        Count independent confirming signal dimensions.
        Maximum 7 points:
        - High impact        (impact_score >= 0.6)
        - High relevance     (relevance_score >= 0.7)
        - Novel event        (novelty_score >= 0.5)
        - Asset match        (affected_assets not empty)
        - Strong sentiment   (abs(sentiment_score) >= 0.6)
        - Price momentum     (change_pct_24h in signal direction >= threshold)  [market data]
        - Volume confirm     (volume_24h >= threshold)                          [market data]
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
        # Market-data dimensions
        score += self._price_momentum_score(market_data.change_pct_24h, direction)
        score += self._volume_confirmation_score(market_data.volume_24h)
        return score

    def _price_momentum_score(
        self, change_pct_24h: float, direction: SignalDirection
    ) -> int:
        """Return 1 if 24h price movement confirms signal direction, else 0."""
        threshold = self._price_momentum_threshold_pct
        if direction == SignalDirection.LONG and change_pct_24h >= threshold:
            return 1
        if direction == SignalDirection.SHORT and change_pct_24h <= -threshold:
            return 1
        return 0

    def _volume_confirmation_score(self, volume_24h: float) -> int:
        """Return 1 if 24h volume meets the confirmation threshold, else 0."""
        return 1 if volume_24h >= self._volume_threshold_usd else 0

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

    def _build_supporting_factors(
        self,
        analysis: AnalysisResult,
        market_data: MarketDataPoint,
        direction: SignalDirection,
    ) -> list[str]:
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
        # Market-data supporting factors
        if self._price_momentum_score(market_data.change_pct_24h, direction):
            factors.append(
                f"Price momentum confirms direction: {market_data.change_pct_24h:+.2f}% 24h"
            )
        if self._volume_confirmation_score(market_data.volume_24h):
            factors.append(
                f"Volume confirmation: ${market_data.volume_24h:,.0f} 24h"
            )
        return factors or ["No specific supporting factors identified"]

    def _build_contradicting_factors(
        self,
        analysis: AnalysisResult,
        market_data: MarketDataPoint,
        direction: SignalDirection,
    ) -> list[str]:
        factors: list[str] = []
        if analysis.spam_probability > 0.3:
            factors.append(f"Elevated spam probability: {analysis.spam_probability:.2f}")
        if analysis.confidence_score < 0.85:
            factors.append(f"Moderate confidence: {analysis.confidence_score:.2f}")
        if not analysis.tags:
            factors.append("No context tags available")
        # Market-data contradicting: price moves against signal direction
        if not self._price_momentum_score(market_data.change_pct_24h, direction):
            if abs(market_data.change_pct_24h) >= self._price_momentum_threshold_pct:
                factors.append(
                    f"Price momentum opposes direction: {market_data.change_pct_24h:+.2f}% 24h"
                )
        if not self._volume_confirmation_score(market_data.volume_24h):
            factors.append(
                f"Low volume (${market_data.volume_24h:,.0f}"
                f" < ${self._volume_threshold_usd:,.0f} threshold)"
            )
        return factors or ["No significant contradicting factors"]
