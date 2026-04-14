"""Alert Service — single entry-point for alert dispatch.

Usage:
    service = AlertService.from_settings(settings)
    results = await service.process_document(doc, result, spam_probability)

process_document():
  - Evaluates threshold (ThresholdEngine)
  - Checks title-based dedup (D-114, cross-run via audit trail)
  - Returns [] if document does not meet threshold or is a duplicate
  - Builds AlertMessage and dispatches to all active channels

send_digest():
  - Sends a pre-built list of AlertMessages as a digest to all channels

from_settings():
  - Factory method — reads AlertSettings and wires channels + threshold
  - In dry_run mode: channels are always included for logging
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog

from app.alerts.audit import (
    ALERT_AUDIT_JSONL_FILENAME,
    AlertAuditRecord,
    append_alert_audit,
    load_alert_audits,
)
from app.alerts.base.interfaces import AlertDeliveryResult, AlertMessage, BaseAlertChannel
from app.alerts.channels.email import EmailAlertChannel
from app.alerts.channels.telegram import TelegramAlertChannel
from app.alerts.eligibility import (
    check_price_trend_alignment,
    evaluate_directional_eligibility,
)
from app.alerts.threshold import ThresholdEngine
from app.analysis.scoring import compute_priority
from app.core.domain.document import AnalysisResult, CanonicalDocument
from app.core.settings import AppSettings
from app.normalization.cleaner import normalize_title, title_hash

log = structlog.get_logger(__name__)

_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]

# D-114: Title-based alert deduplication window.
_DEDUP_LOOKBACK_HOURS = 24
# D-114: Fuzzy dedup — Jaccard word-overlap threshold.
# 0.4 catches cross-source rewrites of the same story while avoiding
# false merges of genuinely different articles.
_FUZZY_JACCARD_THRESHOLD = 0.4

# D-119: Directional asset rate-limit window.
# Max 1 directional alert per asset+sentiment direction per window.
# Prevents cluster-misses where 17 BTC-bullish alerts fire in the same hour
# and all miss because the market context is identical.
_ASSET_RATE_LIMIT_HOURS = 6


class AlertService:
    """Orchestrates threshold evaluation and multi-channel alert delivery."""

    def __init__(
        self,
        channels: list[BaseAlertChannel],
        threshold: ThresholdEngine,
        *,
        dedup_lookback_hours: int = _DEDUP_LOOKBACK_HOURS,
        audit_dir: Path | None = None,
    ) -> None:
        self._channels = channels
        self._threshold = threshold
        self._dedup_lookback_hours = dedup_lookback_hours
        self._audit_dir = audit_dir or (_WORKSPACE_ROOT / "artifacts")
        # In-memory fast path: session-scoped title hashes (exact match)
        self._seen_title_hashes: set[str] = set()
        # Normalized word-sets for fuzzy matching
        self._seen_title_words: list[tuple[str, set[str]]] = []
        # D-119: Asset rate-limit — tracks (asset, sentiment) → last dispatch time
        self._asset_rate_limit: dict[tuple[str, str], datetime] = {}
        # Load recent hashes from audit trail for cross-run dedup
        self._load_recent_title_hashes()

    def _load_recent_title_hashes(self) -> None:
        """Seed in-memory sets from recent audit records (cross-run dedup)."""
        try:
            records = load_alert_audits(self._audit_dir)
        except Exception:
            return
        cutoff = datetime.now(UTC) - timedelta(hours=self._dedup_lookback_hours)
        rate_cutoff = datetime.now(UTC) - timedelta(hours=_ASSET_RATE_LIMIT_HOURS)
        for rec in records:
            try:
                ts = datetime.fromisoformat(rec.dispatched_at.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                continue
            if ts < cutoff:
                continue
            if rec.title_hash is not None:
                self._seen_title_hashes.add(rec.title_hash)
                # Reconstruct word-set from title_hash is not possible,
                # so fuzzy dedup within a run relies on _register_title only.
                # Cross-run fuzzy is seeded from normalized_title stored below.
            if rec.normalized_title:
                words = set(rec.normalized_title.split())
                if words:
                    self._seen_title_words.append((rec.normalized_title, words))
            # D-119: Seed asset rate-limit from recent directional alerts.
            if (
                ts >= rate_cutoff
                and rec.directional_eligible is True
                and rec.sentiment_label
            ):
                sentiment = rec.sentiment_label.lower()
                for asset in rec.affected_assets:
                    key = (asset.upper(), sentiment)
                    existing = self._asset_rate_limit.get(key)
                    if existing is None or ts > existing:
                        self._asset_rate_limit[key] = ts

    def _is_duplicate_title(self, doc_title: str) -> bool:
        """Return True if an exact or fuzzy-similar title was already alerted."""
        if not doc_title:
            return False
        th = title_hash(doc_title)
        if th in self._seen_title_hashes:
            return True
        # Fuzzy: Jaccard word-overlap on normalized title
        norm = normalize_title(doc_title)
        words = set(norm.split())
        if not words:
            return False
        for _seen_norm, seen_words in self._seen_title_words:
            intersection = len(words & seen_words)
            union = len(words | seen_words)
            if union > 0 and intersection / union >= _FUZZY_JACCARD_THRESHOLD:
                return True
        return False

    def _register_title(self, doc_title: str) -> None:
        """Register a title after successful dispatch (exact + fuzzy)."""
        if doc_title:
            self._seen_title_hashes.add(title_hash(doc_title))
            norm = normalize_title(doc_title)
            words = set(norm.split())
            if words:
                self._seen_title_words.append((norm, words))

    def _is_asset_rate_limited(self, message: AlertMessage) -> bool:
        """D-119: Return True if a directional alert for the same asset+sentiment
        was already dispatched within the rate-limit window.

        Prevents cluster-misses where many articles about the same market event
        generate redundant directional alerts in the same time window.
        """
        sentiment = (message.sentiment_label or "").lower()
        if sentiment not in ("bullish", "bearish"):
            return False
        if not message.affected_assets:
            return False
        now = datetime.now(UTC)
        cutoff = now - timedelta(hours=_ASSET_RATE_LIMIT_HOURS)
        for asset in message.affected_assets:
            key = (asset.upper(), sentiment)
            last_dispatch = self._asset_rate_limit.get(key)
            if last_dispatch is not None and last_dispatch > cutoff:
                return True
        return False

    def _register_asset_rate_limit(self, message: AlertMessage) -> None:
        """D-119: Record dispatch time for asset rate-limit tracking."""
        sentiment = (message.sentiment_label or "").lower()
        if sentiment not in ("bullish", "bearish"):
            return
        now = datetime.now(UTC)
        for asset in message.affected_assets:
            key = (asset.upper(), sentiment)
            self._asset_rate_limit[key] = now

    @classmethod
    def from_settings(cls, settings: AppSettings) -> AlertService:
        """Build AlertService from AppSettings.

        Channels are only included when is_enabled=True.
        In dry_run mode, both channels are always added so output appears in logs.
        """
        s = settings.alerts
        channels: list[BaseAlertChannel] = []

        telegram = TelegramAlertChannel(s)
        if telegram.is_enabled or s.dry_run:
            channels.append(telegram)

        email = EmailAlertChannel(s)
        if email.is_enabled or s.dry_run:
            channels.append(email)

        threshold = ThresholdEngine(min_priority=s.min_priority)
        return cls(channels=channels, threshold=threshold)

    async def process_document(
        self,
        doc: CanonicalDocument,
        result: AnalysisResult,
        spam_probability: float = 0.0,
    ) -> list[AlertDeliveryResult]:
        """Evaluate threshold and dispatch alerts for one analyzed document.

        Returns:
            List of AlertDeliveryResult — one per active channel.
            Empty list if the document does not meet the alert threshold
            or has a duplicate title within the dedup window.
        """
        if not self._threshold.should_alert(result, spam_probability=spam_probability):
            return []

        # D-114: Title-based dedup — skip if same story already alerted
        if self._is_duplicate_title(doc.title):
            log.info(
                "alert.skipped_duplicate_title",
                document_id=str(doc.id),
                title_hash=title_hash(doc.title),
            )
            return []

        message = _build_alert_message(doc, result, spam_probability)

        # D-119: Asset rate-limit — max 1 directional alert per asset+sentiment
        # per window to prevent cluster-misses from correlated news events.
        if self._is_asset_rate_limited(message):
            log.info(
                "alert.skipped_asset_rate_limit",
                document_id=str(doc.id),
                sentiment=message.sentiment_label,
                assets=message.affected_assets,
            )
            return []

        # D-118: Price trend divergence gate.
        # If directional-eligible, verify the market trend confirms the
        # sentiment direction before dispatching.  89% of historical misses
        # had correct sentiment but opposite market movement.
        if message.sentiment_label and message.sentiment_label.lower() in (
            "bullish",
            "bearish",
        ):
            trend_blocked = await self._check_price_trend_divergence(
                message, str(doc.id)
            )
            if trend_blocked:
                return []

        deliveries = await self._dispatch(message)
        # Register after dispatch so subsequent docs in same run are caught
        self._register_title(doc.title)
        self._register_asset_rate_limit(message)
        return deliveries

    async def send_digest(
        self,
        messages: list[AlertMessage],
        period: str,
    ) -> list[AlertDeliveryResult]:
        """Send a digest of multiple alerts to all active channels.

        period: human-readable label e.g. "last 60 minutes"
        """
        if not messages:
            return []
        results = []
        audit_path = self._audit_dir / ALERT_AUDIT_JSONL_FILENAME
        for channel in self._channels:
            delivery = await channel.send_digest(messages, period)
            _log_result(delivery, digest=True, document_id="multiple-digest", audit_path=audit_path)
            results.append(delivery)
        return results

    async def _check_price_trend_divergence(
        self,
        message: AlertMessage,
        doc_id: str,
    ) -> bool:
        """Return True if price trend diverges from sentiment (= should block).

        Fail-open: returns False (don't block) if market data is unavailable.
        """
        eligible_assets = list(message.affected_assets)
        if not eligible_assets:
            return False
        try:
            from app.market_data.coingecko_adapter import (
                CoinGeckoAdapter,
                _resolve_symbol,
            )

            # Check the first eligible asset
            resolved = _resolve_symbol(eligible_assets[0])
            if resolved is None:
                return False
            adapter = CoinGeckoAdapter()
            ticker = await adapter.get_ticker(eligible_assets[0])
            if ticker is None:
                return False
            aligned = check_price_trend_alignment(
                message.sentiment_label or "",
                ticker.change_pct_24h,
                ticker.change_pct_7d,
            )
            if not aligned:
                log.info(
                    "alert.directional.price_divergence",
                    document_id=doc_id,
                    sentiment=message.sentiment_label,
                    asset=eligible_assets[0],
                    change_pct_24h=ticker.change_pct_24h,
                    change_pct_7d=ticker.change_pct_7d,
                )
                return True
        except Exception as exc:
            log.warning(
                "alert.directional.price_check_failed",
                document_id=doc_id,
                error=str(exc),
            )
        return False

    async def _dispatch(self, message: AlertMessage) -> list[AlertDeliveryResult]:
        results = []
        audit_path = self._audit_dir / ALERT_AUDIT_JSONL_FILENAME
        for channel in self._channels:
            delivery = await channel.send(message)
            _log_result(delivery, message=message, audit_path=audit_path)
            results.append(delivery)
        return results


def _build_alert_message(
    doc: CanonicalDocument,
    result: AnalysisResult,
    spam_probability: float,
) -> AlertMessage:
    """Build AlertMessage from CanonicalDocument + AnalysisResult."""
    score = compute_priority(result, spam_probability=spam_probability)
    return AlertMessage(
        document_id=str(doc.id),
        title=doc.title,
        url=doc.url,
        priority=score.priority,
        sentiment_label=str(result.sentiment_label),
        actionable=result.actionable,
        explanation=result.explanation_short,
        affected_assets=list(result.affected_assets),
        published_at=doc.published_at,
        source_name=doc.source_name,
        tags=list(result.tags),
        sentiment_score=result.sentiment_score,
        impact_score=result.impact_score,
        directional_confidence=result.directional_confidence,
        event_timing=result.event_timing,
    )


def _log_result(
    result: AlertDeliveryResult,
    *,
    digest: bool = False,
    message: AlertMessage | None = None,
    document_id: str | None = None,
    audit_path: Path | None = None,
) -> None:
    kind = "digest" if digest else "alert"
    doc_id = document_id or (message.document_id if message else None)
    if result.success:
        log.info(
            f"alert.{kind}.sent",
            channel=result.channel,
            message_id=result.message_id,
        )
        # Sprint 21: Append audit trail for operational readiness
        if doc_id:
            sentiment_label = message.sentiment_label if message else None
            affected_assets = list(message.affected_assets) if message else []
            directional_eligible: bool | None = None
            directional_block_reason: str | None = None
            directional_blocked_assets: list[str] = []

            if message is not None:
                eligibility = evaluate_directional_eligibility(
                    sentiment_label=message.sentiment_label,
                    affected_assets=list(message.affected_assets),
                    sentiment_score=message.sentiment_score,
                    impact_score=message.impact_score,
                    title=message.title,
                    directional_confidence=message.directional_confidence,
                    event_timing=message.event_timing,
                    actionable=message.actionable,
                    priority=message.priority,
                    source_name=message.source_name,
                )
                directional_eligible = eligibility.directional_eligible
                directional_block_reason = eligibility.directional_block_reason
                directional_blocked_assets = list(eligibility.blocked_assets)
                if eligibility.is_directional:
                    # Keep only tradeable/supported assets for directional evidence.
                    affected_assets = list(eligibility.eligible_assets)
                    if eligibility.directional_eligible is False:
                        log.info(
                            "alert.directional.blocked",
                            document_id=doc_id,
                            sentiment=message.sentiment_label,
                            block_reason=eligibility.directional_block_reason,
                            blocked_assets=eligibility.blocked_assets,
                        )


            record = AlertAuditRecord(
                document_id=doc_id,
                channel=result.channel,
                message_id=result.message_id,
                is_digest=digest,
                sentiment_label=sentiment_label,
                affected_assets=affected_assets,
                priority=message.priority if message else None,
                actionable=message.actionable if message else None,
                directional_eligible=directional_eligible,
                directional_block_reason=directional_block_reason,
                directional_blocked_assets=directional_blocked_assets,
                title_hash=title_hash(message.title) if message else None,
                normalized_title=normalize_title(message.title) if message else None,
                source_name=message.source_name if message else None,
            )
            effective_path = audit_path or (
                _WORKSPACE_ROOT / "artifacts" / ALERT_AUDIT_JSONL_FILENAME
            )
            append_alert_audit(record, effective_path)
    else:
        log.error(
            f"alert.{kind}.failed",
            channel=result.channel,
            error=result.error,
        )
