"""Directional alert eligibility helpers.

Fail-closed rule:
- Directional sentiment without a tradeable crypto asset mapping is ineligible.
- Eligible assets must resolve to supported CoinGecko symbols.
- Weak signals (low sentiment magnitude or low impact) are blocked to reduce
  false positives in hit-rate tracking (D-111).
- Reactive price narratives (bearish titles describing past moves) are blocked
  to reduce false-positive pollution (D-113).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.market_data.coingecko_adapter import _resolve_symbol

_DIRECTIONAL_SENTIMENTS = frozenset({"bullish", "bearish"})
BLOCK_REASON_MISSING_ASSETS = "missing_affected_assets"
BLOCK_REASON_UNSUPPORTED_ASSETS = "unsupported_or_non_crypto_assets"
BLOCK_REASON_WEAK_SIGNAL = "weak_directional_signal"
BLOCK_REASON_REACTIVE_NARRATIVE = "reactive_price_narrative"
BLOCK_REASON_MAJORITY_NON_CRYPTO = "majority_non_crypto_assets"
BLOCK_REASON_LOW_DIRECTIONAL_CONFIDENCE = "low_directional_confidence"
BLOCK_REASON_PRICE_TREND_DIVERGENCE = "price_trend_divergence"
BLOCK_REASON_NOT_ACTIONABLE = "not_actionable"
BLOCK_REASON_LOW_PRIORITY = "low_priority"

# D-116 / D-119: Minimum directional confidence from LLM analysis.
# Asymmetric thresholds (D-121): bearish alerts had 4% precision (1/25)
# vs bullish 75% (18/24).  Bearish requires near-certain catalyst events
# (hacks, bans, exploits); bullish threshold stays at proven level.
# D-122: Bearish confidence raised from 0.92→0.95 based on 22% precision
# (vs bullish 50%).  Only near-certain adverse events pass.
MIN_DIRECTIONAL_CONFIDENCE_BULLISH = 0.8
MIN_DIRECTIONAL_CONFIDENCE_BEARISH = 0.95

# Directional strength thresholds — alerts below these are excluded from
# directional hit-rate tracking to reduce false-positive pollution.
# D-119: Impact raised from 0.55 to 0.60.  Empirical: low-impact
# directional signals (P7/P10 cluster) had <25% precision.
# D-122: Bearish impact raised from 0.75→0.80 based on 22% precision.
MIN_SENTIMENT_MAGNITUDE = 0.55
MIN_IMPACT_SCORE_BULLISH = 0.60
MIN_IMPACT_SCORE_BEARISH = 0.80

# D-113: Reactive price narrative patterns.
# Bearish alerts whose titles match these describe *past* price moves,
# not predictive events.  Empirical FP rate: 100% on P9/P10 bearish.
# Hits come from actor-action titles ("Firm X sells Y"), never from
# reactive market commentary ("Bitcoin drops/slides/dips/collapses").
_REACTIVE_BEARISH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:drops?|dropped|dropping)\b", re.IGNORECASE),
    re.compile(r"\b(?:dips?|dipped|dipping)\b", re.IGNORECASE),
    re.compile(r"\b(?:slides?|slid|sliding)\b", re.IGNORECASE),
    re.compile(r"\b(?:sinks?|sank|sunk|sinking)\b", re.IGNORECASE),
    re.compile(r"\b(?:collapses?|collapsed|collapsing)\b", re.IGNORECASE),
    re.compile(r"\b(?:plunges?|plunged|plunging)\b", re.IGNORECASE),
    re.compile(r"\b(?:crashes?|crashed|crashing)\b", re.IGNORECASE),
    re.compile(r"\b(?:tumbles?|tumbled|tumbling)\b", re.IGNORECASE),
    re.compile(r"\b(?:falls?|fell|falling)\b", re.IGNORECASE),
    re.compile(r"\b(?:sell[\s-]?off|selloff|sold off)\b", re.IGNORECASE),
    re.compile(r"\b(?:wipeout|wiped out)\b", re.IGNORECASE),
    re.compile(r"\b(?:liquidation)s?\b", re.IGNORECASE),
    re.compile(r"\bhits?\s+(?:new\s+)?(?:low|monthly low|weekly low)\b", re.IGNORECASE),
    re.compile(r"\bextreme\s+fear\b", re.IGNORECASE),
    re.compile(r"\boutflows?\b", re.IGNORECASE),
    re.compile(r"\bweakens?\b", re.IGNORECASE),
    re.compile(r"\bheading\s+for\s+.*(?:collapse|crash|drop)\b", re.IGNORECASE),
)


# D-115: Reactive bullish price narrative patterns.
# Symmetric to bearish: bullish titles describing past price moves
# ("surges", "rallies", "soars") have the same FP problem — the move
# already happened, making the "prediction" a lagging indicator.
_REACTIVE_BULLISH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:surges?|surged|surging)\b", re.IGNORECASE),
    re.compile(r"\b(?:rallies|rallied|rallying|rally)\b", re.IGNORECASE),
    re.compile(r"\b(?:soars?|soared|soaring)\b", re.IGNORECASE),
    re.compile(r"\b(?:jumps?|jumped|jumping)\b", re.IGNORECASE),
    re.compile(r"\b(?:spikes?|spiked|spiking)\b", re.IGNORECASE),
    re.compile(r"\b(?:rockets?|rocketed|rocketing)\b", re.IGNORECASE),
    re.compile(r"\b(?:moons?|mooned|mooning)\b", re.IGNORECASE),
    re.compile(r"\b(?:skyrockets?|skyrocketed|skyrocketing)\b", re.IGNORECASE),
    re.compile(r"\b(?:pumps?|pumped|pumping)\b", re.IGNORECASE),
    re.compile(r"\b(?:explodes?|exploded|exploding)\b", re.IGNORECASE),
    re.compile(
        r"\bhits?\s+(?:new\s+)?(?:high|all.time.high|ATH|monthly high|weekly high)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:breakout|broke out|breaking out)\b", re.IGNORECASE),
    re.compile(r"\binflows?\b", re.IGNORECASE),
)


def _is_reactive_bearish(title: str) -> bool:
    """Return True if the title describes a past/ongoing price decline."""
    for pattern in _REACTIVE_BEARISH_PATTERNS:
        if pattern.search(title):
            return True
    return False


def _is_reactive_bullish(title: str) -> bool:
    """Return True if the title describes a past/ongoing price rise."""
    for pattern in _REACTIVE_BULLISH_PATTERNS:
        if pattern.search(title):
            return True
    return False


def check_price_trend_alignment(
    sentiment: str,
    change_pct_24h: float,
    change_pct_7d: float = 0.0,
    *,
    regime_threshold_7d_bullish: float = 3.0,
    regime_threshold_7d_bearish: float = 1.5,
) -> bool:
    """Return True if the price trend confirms the sentiment direction.

    D-118: Gate dispatching directional alerts on whether the market is
    actually moving in the predicted direction.  89% of historical misses
    were correct-sentiment but wrong-market-context.

    D-120 / D-121: 7d regime gate with asymmetric thresholds.
    Bearish uses a tighter threshold (1.5%) because bearish signals in even
    mildly bullish regimes had 4% precision (1/25).
    Bullish threshold stays at 3.0%.

    Rules:
      - bearish + 7d change > +threshold_bearish → divergent (block)
      - bullish + 7d change < −threshold_bullish → divergent (block)
      - bullish + price rising (24h > 0)  → aligned
      - bearish + price falling (24h < 0) → aligned
      - otherwise                         → divergent (block)
    """
    sentiment_lower = sentiment.strip().lower()

    # D-120 / D-121: 7d regime override — asymmetric thresholds.
    if sentiment_lower == "bearish" and change_pct_7d > regime_threshold_7d_bearish:
        return False
    if sentiment_lower == "bullish" and change_pct_7d < -regime_threshold_7d_bullish:
        return False

    # D-118: 24h directional alignment.
    if sentiment_lower == "bullish":
        return change_pct_24h > 0.0
    if sentiment_lower == "bearish":
        return change_pct_24h < 0.0
    return True  # non-directional: always pass


@dataclass(frozen=True)
class DirectionalEligibilityDecision:
    """Eligibility decision for directional alert operations."""

    is_directional: bool
    directional_eligible: bool | None
    directional_block_reason: str | None = None
    eligible_assets: list[str] = field(default_factory=list)
    blocked_assets: list[str] = field(default_factory=list)


def evaluate_directional_eligibility(
    *,
    sentiment_label: str | None,
    affected_assets: list[str],
    sentiment_score: float | None = None,
    impact_score: float | None = None,
    title: str | None = None,
    directional_confidence: float | None = None,
    event_timing: str | None = None,
    actionable: bool | None = None,
    priority: int | None = None,
) -> DirectionalEligibilityDecision:
    """Return directional eligibility for operational metrics.

    Non-directional sentiments return ``directional_eligible=None``.
    Directional sentiments must pass score-strength gates, a reactive-narrative
    filter (bearish only), AND resolve to at least one supported tradeable
    crypto symbol; otherwise they are blocked.
    """
    sentiment = (sentiment_label or "").strip().lower()
    if sentiment not in _DIRECTIONAL_SENTIMENTS:
        return DirectionalEligibilityDecision(
            is_directional=False,
            directional_eligible=None,
        )

    # D-122: Non-actionable alerts are noise for directional tracking.
    # Empirical: actionable=false had 22% precision vs 52% for actionable=true.
    if actionable is False:
        return DirectionalEligibilityDecision(
            is_directional=True,
            directional_eligible=False,
            directional_block_reason=BLOCK_REASON_NOT_ACTIONABLE,
        )

    # D-122: Low-priority alerts lack predictive value for directional tracking.
    # Empirical: P7 had 21% precision.  Minimum P8 required.
    if priority is not None and priority <= 7:
        return DirectionalEligibilityDecision(
            is_directional=True,
            directional_eligible=False,
            directional_block_reason=BLOCK_REASON_LOW_PRIORITY,
        )

    # Score-strength gates (D-111): block weak directional signals early.
    if sentiment_score is not None and abs(sentiment_score) < MIN_SENTIMENT_MAGNITUDE:
        return DirectionalEligibilityDecision(
            is_directional=True,
            directional_eligible=False,
            directional_block_reason=BLOCK_REASON_WEAK_SIGNAL,
        )
    # D-121: Asymmetric impact threshold — bearish needs higher impact.
    min_impact = (
        MIN_IMPACT_SCORE_BEARISH if sentiment == "bearish" else MIN_IMPACT_SCORE_BULLISH
    )
    if impact_score is not None and impact_score < min_impact:
        return DirectionalEligibilityDecision(
            is_directional=True,
            directional_eligible=False,
            directional_block_reason=BLOCK_REASON_WEAK_SIGNAL,
        )

    # D-113/D-115: Reactive price narrative gate.
    # Titles describing past price moves ("drops", "surges") are lagging
    # indicators, not predictions.  Empirical 0% precision at P9/P10 bearish;
    # symmetric filter for bullish reactive titles (D-115).
    if sentiment == "bearish" and title and _is_reactive_bearish(title):
        return DirectionalEligibilityDecision(
            is_directional=True,
            directional_eligible=False,
            directional_block_reason=BLOCK_REASON_REACTIVE_NARRATIVE,
        )
    if sentiment == "bullish" and title and _is_reactive_bullish(title):
        return DirectionalEligibilityDecision(
            is_directional=True,
            directional_eligible=False,
            directional_block_reason=BLOCK_REASON_REACTIVE_NARRATIVE,
        )

    # D-116 / D-121: Asymmetric directional confidence gate.
    # Bearish requires ≥0.92 (only concrete adverse events); bullish ≥0.8.
    min_confidence = (
        MIN_DIRECTIONAL_CONFIDENCE_BEARISH
        if sentiment == "bearish"
        else MIN_DIRECTIONAL_CONFIDENCE_BULLISH
    )
    if (
        directional_confidence is not None
        and directional_confidence < min_confidence
    ):
        return DirectionalEligibilityDecision(
            is_directional=True,
            directional_eligible=False,
            directional_block_reason=BLOCK_REASON_LOW_DIRECTIONAL_CONFIDENCE,
        )

    # D-116: Backward-looking reports are not predictive signals.
    if event_timing in ("backward_report", "speculative"):
        return DirectionalEligibilityDecision(
            is_directional=True,
            directional_eligible=False,
            directional_block_reason=BLOCK_REASON_REACTIVE_NARRATIVE,
        )

    eligible_assets: list[str] = []
    blocked_assets: list[str] = []
    seen_eligible: set[str] = set()
    seen_blocked: set[str] = set()
    has_non_empty_asset = False

    for raw_asset in affected_assets:
        candidate = raw_asset.strip().upper()
        if not candidate:
            continue
        has_non_empty_asset = True
        resolved = _resolve_symbol(candidate)
        if resolved is None:
            if candidate not in seen_blocked:
                blocked_assets.append(candidate)
                seen_blocked.add(candidate)
            continue
        normalized_symbol, _coin_id = resolved
        if normalized_symbol not in seen_eligible:
            eligible_assets.append(normalized_symbol)
            seen_eligible.add(normalized_symbol)

    if eligible_assets:
        # D-116: Majority non-crypto gate.
        # If more than half of the mentioned assets are non-crypto (equities,
        # ETFs, etc.), the article is primarily about traditional markets and
        # the crypto mention is incidental.  Empirical precision for these
        # is ~0% (COIN, MSTR, IBIT, HOOD, MARA always miss).
        total_assets = len(eligible_assets) + len(blocked_assets)
        if total_assets > 1 and len(blocked_assets) > len(eligible_assets):
            return DirectionalEligibilityDecision(
                is_directional=True,
                directional_eligible=False,
                directional_block_reason=BLOCK_REASON_MAJORITY_NON_CRYPTO,
                eligible_assets=eligible_assets,
                blocked_assets=blocked_assets,
            )
        return DirectionalEligibilityDecision(
            is_directional=True,
            directional_eligible=True,
            eligible_assets=eligible_assets,
            blocked_assets=blocked_assets,
        )

    reason = (
        BLOCK_REASON_MISSING_ASSETS
        if not has_non_empty_asset
        else BLOCK_REASON_UNSUPPORTED_ASSETS
    )
    return DirectionalEligibilityDecision(
        is_directional=True,
        directional_eligible=False,
        directional_block_reason=reason,
        eligible_assets=[],
        blocked_assets=blocked_assets,
    )
