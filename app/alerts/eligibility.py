"""Directional alert eligibility helpers.

Fail-closed rule:
- Directional sentiment without a tradeable crypto asset mapping is ineligible.
- Eligible assets must resolve to supported CoinGecko symbols.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.market_data.coingecko_adapter import _resolve_symbol

_DIRECTIONAL_SENTIMENTS = frozenset({"bullish", "bearish"})
BLOCK_REASON_MISSING_ASSETS = "missing_affected_assets"
BLOCK_REASON_UNSUPPORTED_ASSETS = "unsupported_or_non_crypto_assets"


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
) -> DirectionalEligibilityDecision:
    """Return directional eligibility for operational metrics.

    Non-directional sentiments return ``directional_eligible=None``.
    Directional sentiments must resolve to at least one supported tradeable
    crypto symbol; otherwise they are blocked.
    """
    sentiment = (sentiment_label or "").strip().lower()
    if sentiment not in _DIRECTIONAL_SENTIMENTS:
        return DirectionalEligibilityDecision(
            is_directional=False,
            directional_eligible=None,
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
