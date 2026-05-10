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
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from app.audit.structured_reasoning import (
    PHASE_CONFIDENCE_CHANGE,
    PHASE_INVALIDATION,
    ReasoningJournal,
)
from app.core.domain.document import AnalysisResult
from app.core.enums import SentimentLabel
from app.learning.active_calibrator import ActiveCalibrator
from app.learning.active_threshold import ActiveThreshold
from app.market_data.models import MarketDataPoint
from app.market_data.regime_detection import (
    FeatureName,
    MarketRegime,
    RegimeDetectionEngine,
    make_observation,
)
from app.signals.bayes_journal import append_bayes_report
from app.signals.bayesian_confidence import (
    BayesianConfidenceEngine,
    ConfidenceReport,
    Evidence,
    build_market_regime_evidence,
    build_news_evidence,
    build_volume_evidence,
)
from app.signals.models import (
    SignalCandidate,
    SignalDirection,
    _new_decision_id,
    _now_utc,
)

ExtraEvidencesProvider = Callable[
    [AnalysisResult, MarketDataPoint, SignalDirection], Sequence[Evidence]
]

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
    _PRICE_MOMENTUM_THRESHOLD_PCT: float = 2.0  # |change_24h| >= 2% counts as momentum
    _VOLUME_THRESHOLD_USD: float = 1_000_000.0  # volume_24h >= $1M counts as confirmed

    def __init__(
        self,
        *,
        min_confidence: float = 0.75,
        min_confluence: int = 2,
        market: str = "crypto",
        venue: str = "paper",
        mode: str = "paper",
        model_version: str = "unknown",
        prompt_version: str = "unknown",
        price_momentum_threshold_pct: float | None = None,
        volume_threshold_usd: float | None = None,
        stop_loss_pct: float | None = None,
        take_profit_pct: float | None = None,
        bayes_engine: BayesianConfidenceEngine | None = None,
        bayes_shadow_only: bool = True,
        min_bayes_confidence: float = 0.0,
        max_bayes_uncertainty: float = 1.0,
        bayes_audit_path: Path | str | None = None,
        bayes_extra_evidences_provider: ExtraEvidencesProvider | None = None,
        regime_engine: RegimeDetectionEngine | None = None,
        active_calibrator: ActiveCalibrator | None = None,
        active_min_bayes_confidence: ActiveThreshold | None = None,
        reasoning_journal: ReasoningJournal | None = None,
    ) -> None:
        self._min_confidence = min_confidence
        self._min_confluence = min_confluence
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
            volume_threshold_usd if volume_threshold_usd is not None else self._VOLUME_THRESHOLD_USD
        )
        self._legacy_stop_loss_pct = stop_loss_pct
        self._legacy_take_profit_pct = take_profit_pct
        # Bayes-Pfad — additiv. Engine=None → Schema bleibt bei Legacy-Verhalten.
        self._bayes_engine = bayes_engine
        self._bayes_shadow_only = bayes_shadow_only
        self._min_bayes_confidence = min_bayes_confidence
        self._max_bayes_uncertainty = max_bayes_uncertainty
        self._bayes_audit_path = Path(bayes_audit_path) if bayes_audit_path is not None else None
        self._bayes_extra_evidences_provider = bayes_extra_evidences_provider
        # Optional: ersetzt die simple change_pct_24h-basierte Regime-Ableitung
        # durch die 8-Klassen-RegimeDetectionEngine. Engine-Posterior fließt als
        # source_trust in die Markt-Regime-Evidence — unsichere Klassifikation
        # ⇒ schwächere Evidence statt falscher Confidence.
        self._regime_engine = regime_engine
        # Opt-in: aktiver Calibrator aus dem Learning-Pfad. None = no behavior
        # change (raw Bayes-Posterior wird verwendet wie bisher).
        self._active_calibrator = active_calibrator
        # Opt-in: aktive min-bayes-confidence-Schwelle aus dem Learning-Pfad.
        # None = no behavior change (Constructor-`min_bayes_confidence` wird
        # verwendet wie bisher).
        self._active_min_bayes_confidence = active_min_bayes_confidence
        # Opt-in: structured reasoning journal. Wenn gesetzt, werden
        # Calibrator-Apply (confidence_change) und Bayes-Gate-Reject
        # (invalidation) als ReasoningStep persistiert. None = no audit
        # writes — bestehendes Verhalten bleibt erhalten.
        self._reasoning_journal = reasoning_journal

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
                symbol,
                market_data.price,
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
                symbol,
                analysis.confidence_score,
                self._min_confidence,
            )
            return None

        # Filter 4: must be actionable
        if not analysis.actionable:
            logger.debug("[SIGNAL] Analysis not actionable for %s", symbol)
            return None

        # Filter 5: direction from sentiment (neutral/mixed → no signal)
        direction = self._sentiment_to_direction(analysis)
        if direction is None:
            logger.debug(
                "[SIGNAL] Neutral/mixed sentiment for %s (%s) — no signal",
                symbol,
                analysis.sentiment_label,
            )
            return None

        # Filter 6: confluence check (includes market-data dimensions)
        confluence = self._calculate_confluence(analysis, market_data, direction)
        if confluence < self._min_confluence:
            logger.debug(
                "[SIGNAL] Confluence too low for %s: %d < %d",
                symbol,
                confluence,
                self._min_confluence,
            )
            return None

        # Filter 7 (optional, additiv): Bayesian Confidence-Gate.
        # Engine berechnet immer einen Report, wenn aktiviert.  Ablehnung nur,
        # wenn ``shadow_only=False`` und Confidence unter / Uncertainty über
        # operator-konfigurierter Schwelle liegt.  Bei Schatten-Modus werden
        # die Werte nur angeheftet → Vergleichbarkeit ohne Verhaltensänderung.
        raw_bayes_report = self._evaluate_bayes(analysis, market_data, direction)

        # decision_id wird hier (vor dem Bayes-Gate) generiert, damit auch
        # ein abgelehntes Signal eine Reasoning-Trail-Identität trägt.
        # Bei Approve trägt der SignalCandidate dieselbe ID weiter.
        decision_id = _new_decision_id()

        # Opt-in: aktiver Calibrator korrigiert posterior + abgeleitete
        # confidence vor dem Gate. Der RAW Report bleibt unverändert für das
        # bayes_audit-Append weiter unten (sonst würde das nächste Lern-
        # Run zirkulär auf bereits-kalibrierten Werten lernen).
        bayes_report = raw_bayes_report
        if (
            raw_bayes_report is not None
            and self._active_calibrator is not None
            and self._active_calibrator.is_active
        ):
            bayes_report = self._active_calibrator.apply_to_report(
                raw_bayes_report,
                direction=direction.value,
                regime=self._derive_market_regime(market_data.change_pct_24h),
            )
            logger.debug(
                "[SIGNAL] Calibrator %s applied to %s: "
                "posterior %.4f → %.4f, confidence %.4f → %.4f",
                self._active_calibrator.version_id,
                symbol,
                raw_bayes_report.posterior_probability,
                bayes_report.posterior_probability,
                raw_bayes_report.confidence_score,
                bayes_report.confidence_score,
            )
            # Structured-reasoning step: confidence_change
            if self._reasoning_journal is not None:
                version_id = self._active_calibrator.version_id
                self._reasoning_journal.log_step(
                    decision_id=decision_id,
                    phase=PHASE_CONFIDENCE_CHANGE,
                    actor="ActiveCalibrator",
                    rationale_summary=(
                        f"posterior calibrated for {direction.value} "
                        f"in regime={self._derive_market_regime(market_data.change_pct_24h)}"
                    ),
                    inputs={
                        "raw_posterior": raw_bayes_report.posterior_probability,
                        "regime": self._derive_market_regime(market_data.change_pct_24h),
                        "direction": direction.value,
                    },
                    outputs={
                        "calibrated_posterior": bayes_report.posterior_probability,
                        "calibrated_confidence": bayes_report.confidence_score,
                    },
                    confidence_before=raw_bayes_report.confidence_score,
                    confidence_after=bayes_report.confidence_score,
                    parameter_versions=(
                        {self._active_calibrator.parameter_path: version_id}
                        if version_id is not None
                        else {}
                    ),
                    evidence_refs=(f"bayes_audit:{decision_id}",),
                )

        # Effective min-bayes-confidence: ActiveThreshold (wenn aktiv) sonst
        # Constructor-Default. Audit-friendly: wir loggen den Threshold-Wert,
        # damit der Operator nachvollziehen kann, gegen welche Schwelle gegated
        # wurde.
        effective_min_bayes_confidence = (
            self._active_min_bayes_confidence.value
            if self._active_min_bayes_confidence is not None
            and self._active_min_bayes_confidence.is_active
            else self._min_bayes_confidence
        )

        if (
            bayes_report is not None
            and not self._bayes_shadow_only
            and (
                bayes_report.confidence_score < effective_min_bayes_confidence
                or bayes_report.uncertainty_score > self._max_bayes_uncertainty
            )
        ):
            logger.debug(
                "[SIGNAL] Bayes-gate rejected %s: "
                "confidence=%.2f<min=%.2f or uncertainty=%.2f>max=%.2f",
                symbol,
                bayes_report.confidence_score,
                effective_min_bayes_confidence,
                bayes_report.uncertainty_score,
                self._max_bayes_uncertainty,
            )
            # Structured-reasoning step: invalidation (gate-reject)
            if self._reasoning_journal is not None:
                self._reasoning_journal.log_step(
                    decision_id=decision_id,
                    phase=PHASE_INVALIDATION,
                    actor="SignalGenerator.bayes_gate",
                    rationale_summary=(
                        f"bayes-gate rejected {symbol}: confidence "
                        f"{bayes_report.confidence_score:.4f} vs. min "
                        f"{effective_min_bayes_confidence:.4f}"
                    ),
                    inputs={
                        "confidence_score": bayes_report.confidence_score,
                        "uncertainty_score": bayes_report.uncertainty_score,
                        "min_bayes_confidence": effective_min_bayes_confidence,
                        "max_bayes_uncertainty": self._max_bayes_uncertainty,
                    },
                    outputs={"reason": "bayes_gate_rejected"},
                    parameter_versions=(
                        {
                            self._active_min_bayes_confidence.parameter_path: (
                                self._active_min_bayes_confidence.version_id or ""
                            )
                        }
                        if self._active_min_bayes_confidence is not None
                        and self._active_min_bayes_confidence.is_active
                        else {}
                    ),
                )
            return None

        # Derive market context
        change = market_data.change_pct_24h
        market_regime = self._derive_market_regime(change)
        volatility_state = self._derive_volatility_state(change)

        # Entry levels
        entry_price = market_data.price
        stop_loss_price, take_profit_price = self._legacy_static_risk_bounds(
            entry_price,
            direction,
        )

        # Build narrative factors
        supporting = self._build_supporting_factors(analysis, market_data, direction)
        contradicting = self._build_contradicting_factors(analysis, market_data, direction)

        # Invalidation
        invalidation = "Analysis thesis reversed or invalidated by dynamic risk bounds"

        # Risk summary
        risk_assessment = (
            f"{'Long' if direction == SignalDirection.LONG else 'Short'} entry at "
            f"{entry_price:.4f}. Dynamic SL/TP from RiskEngine."
        )

        # decision_id wurde bereits in Filter 7 generiert (siehe oben), damit
        # auch ein abgelehntes Signal eine Reasoning-Trail-Identität trägt.
        # Audit immer den RAW Report — nicht den (ggf.) calibrated. Sonst
        # wird der nächste Calibration-Run zirkulär.
        if raw_bayes_report is not None and self._bayes_audit_path is not None:
            append_bayes_report(
                decision_id=decision_id,
                symbol=symbol,
                direction=direction.value,
                report=raw_bayes_report,
                path=self._bayes_audit_path,
            )

        return SignalCandidate(
            decision_id=decision_id,
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
            max_loss_estimate_pct=0.0,
            data_sources_used=(market_data.source,),
            source_document_id=analysis.document_id,
            model_version=self._model_version,
            prompt_version=self._prompt_version,
            bayes_prior_probability=(
                bayes_report.prior_probability if bayes_report is not None else None
            ),
            bayes_posterior_probability=(
                bayes_report.posterior_probability if bayes_report is not None else None
            ),
            bayes_confidence_score=(
                bayes_report.confidence_score if bayes_report is not None else None
            ),
            bayes_uncertainty_score=(
                bayes_report.uncertainty_score if bayes_report is not None else None
            ),
            bayes_evidence_weight=(
                bayes_report.evidence_weight if bayes_report is not None else None
            ),
        )

    # ─── Bayes integration ────────────────────────────────────────────────────

    def _evaluate_bayes(
        self,
        analysis: AnalysisResult,
        market_data: MarketDataPoint,
        direction: SignalDirection,
    ) -> ConfidenceReport | None:
        """Übersetze AnalysisResult + MarketDataPoint → ConfidenceReport.

        Phase-1-Mapping (additiv, keine Live-Adapter für Funding/OI/Liq/On-Chain):
          - News: relevance × |sentiment_score|, source_trust = 1 − spam_probability
          - Volume: z-Score-Proxy aus volume_24h vs. _VOLUME_THRESHOLD_USD,
            aligned = price_momentum_score
          - Marktregime: trending_with / trending_against / ranging / volatile
            (von _derive_market_regime + Direction-Alignment abgeleitet)

        Funding/OI/Liquidations/On-Chain bleiben absichtlich leer, bis
        dedizierte Adapter Daten liefern.  Engine ist additiv: weniger
        Evidence ⇒ höhere uncertainty, nicht falsche Confidence.
        """
        if self._bayes_engine is None:
            return None

        evidences = []
        observed_at = datetime.now(UTC)
        # Sentiment-Alignment ist per Konstruktion immer positiv (direction folgt
        # aus sentiment_label).  Wir nutzen |sentiment_score| als Stärkemaß.
        relevance_strength = max(0.0, min(1.0, analysis.relevance_score))
        news_trust = max(0.0, min(1.0, 1.0 - analysis.spam_probability))
        evidences.append(
            build_news_evidence(
                relevance=relevance_strength,
                sentiment_aligned_with_signal=True,
                source_trust=news_trust,
                observed_at=observed_at,
                source_id=analysis.document_id,
            )
        )

        # Volume z-Proxy: 0 bei threshold, +1 bei 4× threshold, −1 bei 0 vol.
        threshold = max(self._volume_threshold_usd, 1.0)
        z_proxy = max(-3.0, min(3.0, (market_data.volume_24h - threshold) / threshold))
        price_aligned = bool(self._price_momentum_score(market_data.change_pct_24h, direction))
        evidences.append(
            build_volume_evidence(
                volume_zscore=z_proxy,
                price_move_aligned_with_signal=price_aligned,
                source_trust=1.0,
                observed_at=observed_at,
                source_id=market_data.source,
            )
        )

        regime_label, regime_trust, regime_source_id = self._regime_for_bayes(
            analysis=analysis,
            market_data=market_data,
            direction=direction,
            price_aligned=price_aligned,
        )
        evidences.append(
            build_market_regime_evidence(
                regime=regime_label,
                source_trust=regime_trust,
                observed_at=observed_at,
                source_id=regime_source_id,
            )
        )

        # Externe Evidences (Funding, OI, Liquidations, On-Chain) — optional.
        # Provider liegt beim Caller, weil er u. U. async I/O braucht.
        if self._bayes_extra_evidences_provider is not None:
            try:
                extras = self._bayes_extra_evidences_provider(analysis, market_data, direction)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[SIGNAL] bayes extra-evidences provider failed: %s", exc)
                extras = ()
            evidences.extend(extras)

        return self._bayes_engine.evaluate(
            evidences,
            prior_probability=analysis.confidence_score,
            now=observed_at,
        )

    # Mapping 8-Klassen-RegimeDetectionEngine → 5-Klassen-Bayes-Label.
    # Logik:
    #   bullish_regimes (BULL, ACCUMULATION) → pro für LONG, contra für SHORT
    #   bearish_regimes (BEAR, DISTRIBUTION, PANIC, EUPHORIC_BLOWOFF) → invers
    #     (EUPHORIC_BLOWOFF und PANIC sind beide *Risiko-Regime gegen LONG*:
    #      Blowoff = Top-Bildung, Panic = Sell-Druck.)
    #   LOW_LIQUIDITY → "ranging" (kein Trend, schwache Confidence)
    #   HIGH_MANIPULATION → "volatile" (Risk-Warnung beidseitig)
    _BULLISH_REGIMES = frozenset(
        {MarketRegime.BULL, MarketRegime.ACCUMULATION}
    )
    _BEARISH_REGIMES = frozenset(
        {
            MarketRegime.BEAR,
            MarketRegime.DISTRIBUTION,
            MarketRegime.PANIC,
            MarketRegime.EUPHORIC_BLOWOFF,
        }
    )

    def _regime_for_bayes(
        self,
        *,
        analysis: AnalysisResult,
        market_data: MarketDataPoint,
        direction: SignalDirection,
        price_aligned: bool,
    ) -> tuple[str, float, str]:
        """Liefere (label, source_trust, source_id) für die Regime-Evidence.

        Default (engine=None): das vorherige change_pct_24h-Heuristik-Mapping
        mit source_trust=1.0.  Mit aktivierter ``regime_engine``: 8-Klassen-
        Klassifikation aus VOLATILITY-Proxy + SOCIAL_MOMENTUM-Proxy, Mapping
        auf die 5-Klassen-Bayes-Sprache, Engine-Posterior wird zum
        ``source_trust`` (unsichere Klassifikation ⇒ schwächere Evidence).
        """
        if self._regime_engine is None:
            heuristic = self._derive_market_regime(market_data.change_pct_24h)
            if heuristic == "trending":
                label = "trending_with" if price_aligned else "trending_against"
            elif heuristic == "volatile":
                label = "volatile"
            else:
                label = "ranging"
            return label, 1.0, market_data.source

        # 2 von 8 Features ableitbar; Rest bleibt 0 (= neutral, Engine
        # vergibt automatisch höhere uncertainty).
        vol_z = max(-3.0, min(3.0, market_data.change_pct_24h / 4.0))
        social_z = max(-3.0, min(3.0, analysis.sentiment_score * 2.0))
        observation = make_observation(
            features={
                FeatureName.VOLATILITY: abs(vol_z),
                FeatureName.SOCIAL_MOMENTUM: social_z,
            },
        )
        report = self._regime_engine.classify(observation)
        primary = report.classification.primary_regime
        if primary in self._BULLISH_REGIMES:
            label = "trending_with" if direction == SignalDirection.LONG else "trending_against"
        elif primary in self._BEARISH_REGIMES:
            label = "trending_with" if direction == SignalDirection.SHORT else "trending_against"
        elif primary == MarketRegime.LOW_LIQUIDITY:
            label = "ranging"
        else:  # HIGH_MANIPULATION
            label = "volatile"
        # Engine-Posterior als source_trust ⇒ schwache Klassifikation dämpft Evidence.
        # Anomaly-Score zieht zusätzlich runter.
        trust = max(
            0.0,
            min(
                1.0,
                report.classification.primary_probability * (1.0 - report.anomaly_score),
            ),
        )
        return label, trust, f"regime_engine:{primary.value}"

    # ─── Private helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _sentiment_to_direction(analysis: AnalysisResult) -> SignalDirection | None:
        if analysis.event_type and analysis.event_type.strip().lower() == "cancel":
            return SignalDirection.CANCEL
        label = analysis.sentiment_label
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

    def _price_momentum_score(self, change_pct_24h: float, direction: SignalDirection) -> int:
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

    def _legacy_static_risk_bounds(
        self,
        entry_price: float,
        direction: SignalDirection,
    ) -> tuple[float | None, float | None]:
        """Return legacy percent SL/TP when configured, else defer to RiskEngine ATR."""
        stop_loss_price = None
        take_profit_price = None
        if self._legacy_stop_loss_pct is not None:
            stop_loss_delta = entry_price * (self._legacy_stop_loss_pct / 100.0)
            stop_loss_price = (
                entry_price - stop_loss_delta
                if direction == SignalDirection.LONG
                else entry_price + stop_loss_delta
            )
        if self._legacy_take_profit_pct is not None:
            take_profit_delta = entry_price * (self._legacy_take_profit_pct / 100.0)
            take_profit_price = (
                entry_price + take_profit_delta
                if direction == SignalDirection.LONG
                else entry_price - take_profit_delta
            )
        return stop_loss_price, take_profit_price



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
            factors.append(f"Volume confirmation: ${market_data.volume_24h:,.0f} 24h")
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
