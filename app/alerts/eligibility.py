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

# Directional strength thresholds — alerts below these are excluded from
# directional hit-rate tracking to reduce false-positive pollution.
MIN_SENTIMENT_MAGNITUDE = 0.55
MIN_IMPACT_SCORE = 0.55

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


def _is_reactive_bearish(title: str) -> bool:
    """Return True if the title describes a past/ongoing price decline."""
    for pattern in _REACTIVE_BEARISH_PATTERNS:
        if pattern.search(title):
            return True
    return False


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

    # Score-strength gates (D-111): block weak directional signals early.
    if sentiment_score is not None and abs(sentiment_score) < MIN_SENTIMENT_MAGNITUDE:
        return DirectionalEligibilityDecision(
            is_directional=True,
            directional_eligible=False,
            directional_block_reason=BLOCK_REASON_WEAK_SIGNAL,
        )
    if impact_score is not None and impact_score < MIN_IMPACT_SCORE:
        return DirectionalEligibilityDecision(
            is_directional=True,
            directional_eligible=False,
            directional_block_reason=BLOCK_REASON_WEAK_SIGNAL,
        )

    # D-113: Reactive price narrative gate (bearish only).
    # Bearish titles describing past price moves ("drops", "slides", "dips")
    # have empirical 0% precision at P9/P10.  Block them early.
    if sentiment == "bearish" and title and _is_reactive_bearish(title):
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
