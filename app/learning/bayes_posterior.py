"""Bayes-Posterior-Update for trade-outcome-conditioned hit-rate beliefs.

Goal-pin 2026-05-16 V4 (operator-spec'd 17:30 CEST):
- Hit definition: trade_pnl_usd > fee_usd → "hit", < -fee_usd → "miss",
  |pnl| ≤ fee → "inconclusive" (Fee-Rauschen ignoriert, kein Update).
- Decay: lifetime + parallel rolling-90d posteriors. Two readouts so the
  operator can spot regime drift before it dominates the lifetime mean.
- Granularity: per (source, symbol, direction) bucket.
- Prior: Beta(2, 2) — soft uniform, max entropy near 0.5. Cold-start
  buckets sit at posterior_mean = 0.5 with wide credible interval.

KAI-no-prediction-rule (memory feedback_kai_no_prediction): the posterior
is a LIKELIHOOD over past observed hit-rate. It is NOT a prediction of
future hits. Consumers (eligibility, sizing) read it as "this bucket has
historically hit X% with 95% credible interval [L, U]", never "this signal
will hit X%".

Memory-Falle paper_audit_pnl_field_semantics: ``realized_pnl_usd`` is
cumulative portfolio-PnL (legacy alias). ONLY ``trade_pnl_usd`` from
``position_closed`` events is per-trade. This module reads only the
correct field — the recalc script enforces the source side.

Math:
- Beta(α, β) conjugate update on Bernoulli(p_hit).
- After observing h hits and m misses: α_post = α_prior + h, β_post = β_prior + m.
- Posterior mean = α_post / (α_post + β_post).
- 95% credible interval approximated via Wilson score on (hits, hits+miss)
  — matches V1 source_reliability convention for visual comparability.
  The Wilson approximation is excellent for moderate-to-large n and avoids
  a scipy dependency for an inverse-Beta-CDF.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

Direction = Literal["long", "short"]
Outcome = Literal["hit", "miss", "inconclusive"]

# Beta(2,2) = soft prior centered at 0.5 with finite variance.
# Choice rationale: empirical baseline (ph5_feature_analysis) puts
# Forward-Precision in the 25-55% band. Beta(2,2) lets a single observation
# move the posterior noticeably, but multiple observations dominate
# quickly — exactly the cold-start behaviour we want.
DEFAULT_PRIOR_ALPHA: float = 2.0
DEFAULT_PRIOR_BETA: float = 2.0

# 90 days mirrors V1 source_reliability rolling window. Same horizon
# avoids "metric A says X, metric B says Y because they look at different
# slices" confusion.
DEFAULT_ROLLING_WINDOW_DAYS: int = 90

# Source label for trades whose open-side had no source_name attached
# (typical for non-premium TradingLoop output). Bucket-key prefix
# makes "what is unsourced telling us?" a separate question from
# "how reliable is provider X?".
UNSOURCED_LABEL: str = "tradingloop"


@dataclass(frozen=True)
class TradeOutcome:
    """Per-trade outcome row ready for Bayes update.

    Built by ``scripts/bayes_posterior_recalc.py`` from
    ``paper_execution_audit.jsonl`` ``position_closed`` events.
    """

    fill_id: str
    timestamp_utc: str
    source: str  # UNSOURCED_LABEL when open had no source
    symbol: str
    direction: Direction
    trade_pnl_usd: float
    fee_usd: float
    outcome: Outcome
    reason: str  # "take" / "stop" / "manual" — forensic only


def _isfinite(x: object) -> bool:
    try:
        return isinstance(x, int | float) and math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def classify_outcome(*, trade_pnl_usd: float, fee_usd: float) -> Outcome:
    """Classify a trade outcome per V4-spec (Hit-Definition B + break-even-inconclusive).

    Decision matrix:
      pnl >  |fee|  → hit          (clear gain after costs)
      pnl < -|fee|  → miss         (clear loss after costs)
      |pnl| ≤ |fee| → inconclusive (fee-noise band — no posterior update)

    The fee-noise band intentionally biases toward inconclusive on small
    trades. Inconclusive trades do NOT decrease the posterior — they are
    simply excluded from the count. This prevents fee-rounding noise from
    polluting the hit-rate estimate.

    Inputs that are non-finite (NaN, inf, missing) classify as inconclusive
    so a malformed audit row never moves the posterior in either direction.
    """
    if not _isfinite(trade_pnl_usd) or not _isfinite(fee_usd):
        return "inconclusive"
    fee_abs = abs(fee_usd)
    if trade_pnl_usd > fee_abs:
        return "hit"
    if trade_pnl_usd < -fee_abs:
        return "miss"
    return "inconclusive"


def beta_update(
    prior_alpha: float,
    prior_beta: float,
    outcomes: Iterable[Outcome],
) -> tuple[float, float, int, int, int]:
    """Beta-Bernoulli conjugate update.

    Returns ``(alpha_post, beta_post, hits, miss, inconclusive_count)``.
    Inconclusive outcomes do NOT update the posterior — they only count
    for forensic reporting. This matches the V1 source_reliability
    convention (where inconclusive is also excluded from Wilson).
    """
    alpha = prior_alpha
    beta = prior_beta
    hits = 0
    miss = 0
    inc = 0
    for o in outcomes:
        if o == "hit":
            alpha += 1
            hits += 1
        elif o == "miss":
            beta += 1
            miss += 1
        else:
            inc += 1
    return alpha, beta, hits, miss, inc


def _wilson_95(hits: int, n: int) -> tuple[float | None, float | None]:
    """Wilson 95% interval on a binomial proportion.

    Returns ``(lower, upper)``; both ``None`` when ``n <= 0``.
    """
    if n <= 0:
        return None, None
    hits_clamped = max(0, min(hits, n))
    p_hat = hits_clamped / n
    z = 1.96
    z_sq = z * z
    denom = 1.0 + z_sq / n
    center = p_hat + z_sq / (2.0 * n)
    inner = p_hat * (1.0 - p_hat) / n + z_sq / (4.0 * n * n)
    margin = z * math.sqrt(max(0.0, inner))
    lower = (center - margin) / denom
    upper = (center + margin) / denom
    return max(0.0, min(1.0, lower)), max(0.0, min(1.0, upper))


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    cleaned = ts.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def build_posterior_report(
    outcomes: list[TradeOutcome],
    *,
    rolling_window_days: int = DEFAULT_ROLLING_WINDOW_DAYS,
    prior_alpha: float = DEFAULT_PRIOR_ALPHA,
    prior_beta: float = DEFAULT_PRIOR_BETA,
    now_utc: datetime | None = None,
) -> dict[str, object]:
    """Compute lifetime + rolling-window posteriors per (source, symbol, direction).

    Returns a JSON-serialisable dict the recalc script writes to
    ``artifacts/bayes_posterior_state.json``. Each bucket carries both
    horizons so the operator can compare:
      - lifetime: stable, high-n, slow to react
      - rolling: recent, lower-n, regime-aware
    Significant divergence between the two is itself a signal ("source X
    used to be 60% hit-rate, last 90d only 30% — regime change?").
    """
    now = now_utc or datetime.now(UTC)
    cutoff = now - timedelta(days=rolling_window_days)

    buckets_lifetime: dict[tuple[str, str, Direction], list[Outcome]] = {}
    buckets_rolling: dict[tuple[str, str, Direction], list[Outcome]] = {}
    for tc in outcomes:
        key = (tc.source.lower(), tc.symbol.upper(), tc.direction)
        buckets_lifetime.setdefault(key, []).append(tc.outcome)
        ts = _parse_iso(tc.timestamp_utc)
        if ts is not None and ts >= cutoff:
            buckets_rolling.setdefault(key, []).append(tc.outcome)

    all_keys = sorted(set(buckets_lifetime.keys()) | set(buckets_rolling.keys()))
    out_buckets: dict[str, dict[str, object]] = {}
    for key in all_keys:
        source, symbol, direction = key
        bucket_id = f"{source}::{symbol}::{direction}"

        lt_outcomes = buckets_lifetime.get(key, [])
        lt_alpha, lt_beta, lt_hits, lt_miss, lt_inc = beta_update(
            prior_alpha, prior_beta, lt_outcomes
        )
        lt_n = lt_hits + lt_miss
        lt_lower, lt_upper = _wilson_95(lt_hits, lt_n)

        rl_outcomes = buckets_rolling.get(key, [])
        rl_alpha, rl_beta, rl_hits, rl_miss, rl_inc = beta_update(
            prior_alpha, prior_beta, rl_outcomes
        )
        rl_n = rl_hits + rl_miss
        rl_lower, rl_upper = _wilson_95(rl_hits, rl_n)

        out_buckets[bucket_id] = {
            "source": source,
            "symbol": symbol,
            "direction": direction,
            "lifetime": {
                "n": lt_n,
                "hits": lt_hits,
                "miss": lt_miss,
                "inconclusive": lt_inc,
                "alpha": lt_alpha,
                "beta": lt_beta,
                "posterior_mean": lt_alpha / (lt_alpha + lt_beta),
                "wilson_lower_95": lt_lower,
                "wilson_upper_95": lt_upper,
            },
            "rolling_90d": {
                "n": rl_n,
                "hits": rl_hits,
                "miss": rl_miss,
                "inconclusive": rl_inc,
                "alpha": rl_alpha,
                "beta": rl_beta,
                "posterior_mean": rl_alpha / (rl_alpha + rl_beta),
                "wilson_lower_95": rl_lower,
                "wilson_upper_95": rl_upper,
            },
        }

    return {
        "schema_version": "v1",
        "report_type": "bayes_posterior_state",
        "generated_at": now.isoformat(),
        "config": {
            "hit_definition": "paper_pnl_after_fee",
            "break_even_treatment": "inconclusive_fee_band",
            "prior_alpha": prior_alpha,
            "prior_beta": prior_beta,
            "rolling_window_days": rolling_window_days,
            "unsourced_label": UNSOURCED_LABEL,
        },
        "n_buckets": len(out_buckets),
        "n_outcomes_total": len(outcomes),
        "buckets": out_buckets,
    }


__all__ = [
    "DEFAULT_PRIOR_ALPHA",
    "DEFAULT_PRIOR_BETA",
    "DEFAULT_ROLLING_WINDOW_DAYS",
    "Direction",
    "Outcome",
    "TradeOutcome",
    "UNSOURCED_LABEL",
    "beta_update",
    "build_posterior_report",
    "classify_outcome",
]
