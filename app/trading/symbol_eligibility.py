"""Symbol-Eligibility — pure structural verdict whether a symbol is usable.

Auto-computed counterpart to the operator-curated ``asset_universe``: decides,
from metrics measured against the CANONICAL venue (Binance — where edge is
measured/resolved), whether a symbol is structurally usable. NO directional /
momentum / edge judgement — only "structurally usable" vs "not".

Honesty-Contract (KAI rule "fehlende Daten = nicht bewertbar, niemals
schätzen"): if a metric is ``None`` it counts against eligibility; a symbol
with NO canonical-venue data at all is ineligible with a single explicit reason
(this is how off-Binance symbols like SLX/VELVET fall out without a separate
exchangeInfo gate).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from app.trading.asset_universe import base_symbol

_QUOTE_RANK = {"USDT": 0, "USDC": 1, "USD": 2}


def _canonical_sort_key(symbol: str) -> tuple[int, int, str]:
    """Lower wins: preferred quote first, spot before perp, then lexical."""
    s = symbol.strip().upper()
    is_perp = 1 if ":" in s else 0
    # Quote = segment after '/', before any ':' (perp suffix).
    quote = s.split("/", 1)[1].split(":", 1)[0] if "/" in s else ""
    return (_QUOTE_RANK.get(quote, 9), is_perp, s)


def resolve_duplicates(symbols: list[str]) -> dict[str, str]:
    """Map each symbol to the canonical variant of its base (pure)."""
    groups: dict[str, list[str]] = defaultdict(list)
    for s in symbols:
        groups[base_symbol(s)].append(s)
    out: dict[str, str] = {}
    for members in groups.values():
        canonical = min(members, key=_canonical_sort_key)
        for m in members:
            out[m] = canonical
    return out

DEFAULT_MIN_TURNOVER_USD: float = 10_000_000.0
DEFAULT_MIN_HISTORY_DAYS: int = 30


@dataclass(frozen=True)
class SymbolMetrics:
    """Canonical-venue metrics for one symbol. ``None`` = not measurable."""

    symbol: str
    base: str
    quote: str
    turnover_24h_usd: float | None
    history_days: int | None


@dataclass(frozen=True)
class EligibilityVerdict:
    """Structural verdict. ``reasons`` is empty iff eligible."""

    symbol: str
    eligible: bool
    reasons: list[str]


def evaluate_eligibility(
    metrics: SymbolMetrics,
    *,
    min_turnover_usd: float = DEFAULT_MIN_TURNOVER_USD,
    min_history_days: int = DEFAULT_MIN_HISTORY_DAYS,
    duplicate_of: str | None = None,
) -> EligibilityVerdict:
    """Decide structural eligibility (pure, deterministic)."""
    # No canonical-venue data at all → single explicit reason (off-venue).
    if metrics.turnover_24h_usd is None and metrics.history_days is None:
        return EligibilityVerdict(metrics.symbol, False, ["no_canonical_venue_data"])

    reasons: list[str] = []

    if duplicate_of is not None and duplicate_of != metrics.symbol:
        reasons.append(f"duplicate_of:{duplicate_of}")

    if metrics.turnover_24h_usd is None:
        reasons.append("no_turnover_data")
    elif metrics.turnover_24h_usd < min_turnover_usd:
        reasons.append("below_min_turnover")

    if metrics.history_days is None:
        reasons.append("no_history_data")
    elif metrics.history_days < min_history_days:
        reasons.append("below_min_history")

    return EligibilityVerdict(metrics.symbol, not reasons, reasons)
