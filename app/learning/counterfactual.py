"""Counterfactual-Evaluation für Calibrator-Vorschläge.

Frage: *„Hätte dieser neue Calibrator, hätte er damals schon im Risk-Gate
gesteckt, welche der real ausgeführten Trades verworfen — und welcher
realisierte P&L wäre damit weggefallen oder vermieden worden?"*

Diese Engine rechnet das auf den **tatsächlich gelaufenen Trade-Outcomes**
nach.  Sie simuliert keine Marktdaten, generiert keine neuen Signale —
denn beides würde Lookahead-Bias und Phantom-Trades einführen, die wir
nicht ehrlich validieren können.

Selection-Bias-Hinweis (dokumentiert)
-------------------------------------

Wir können nur Trades bewerten, die unter dem **damaligen** Risk-Gate
durchgegangen sind und damit ein realisiertes Ergebnis haben.  Trades, die
ein neuer Calibrator *zusätzlich* zugelassen hätte (heute geblockte, neu
durchwinkbare), bleiben außen vor — ihr hypothetischer P&L ist
unbeobachtbar. Die Decision-Logik ist deshalb deliberately **downward-only**:

  • approve   → der Calibrator hätte netto Geld gespart (Δ > Schwelle)
  • reject    → der Calibrator hätte netto Geld gekostet
  • neutral   → kein signifikanter Effekt
  • insufficient_data → zu wenig Trades für eine belastbare Aussage

Side-Awareness
--------------

Bayes-Reports liefern ``posterior_probability`` aus Sicht der
Long-Hypothese; ein Short-Signal wird über ``1 − posterior`` gespiegelt
(Konsistenz mit ``app/learning/calibration_loader.py``).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.learning.recalibration import IdentityCalibrator, LogitLinearCalibrator

DEFAULT_THRESHOLD: Final[float] = 0.75
DEFAULT_MIN_TRADES: Final[int] = 30
DEFAULT_MIN_PNL_IMPROVEMENT_USD: Final[float] = 0.0

DecisionLiteral = Literal["approve", "reject", "neutral", "insufficient_data"]
DirectionLiteral = Literal["long", "short"]


# ─── Inputs ───────────────────────────────────────────────────────────────────


class TradeOutcome(BaseModel):
    """Eine realisierte Trade-Vergangenheit + ihr Bayes-Posterior.

    Die einfachste Form, die für Counterfactual-Analyse nötig ist:
    decision_id (Audit-Anker), Richtung, der raw posterior-Wert (vor
    Calibrator), und der realisierte $-P&L (signed).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: str
    direction: DirectionLiteral
    raw_posterior: float = Field(ge=0.0, le=1.0)
    realized_pnl_usd: float
    timestamp_utc: datetime | None = None


class CounterfactualConfig(BaseModel):
    """Schwellen für die Counterfactual-Decision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    threshold: float = Field(default=DEFAULT_THRESHOLD, ge=0.0, le=1.0)
    min_trades: int = Field(default=DEFAULT_MIN_TRADES, ge=1)
    min_pnl_improvement_usd: float = Field(default=DEFAULT_MIN_PNL_IMPROVEMENT_USD, ge=0.0)
    side_aware: bool = True


# ─── Outputs ──────────────────────────────────────────────────────────────────


class TradeCounterfactual(BaseModel):
    """Pro-Trade-Befund: würde der Trade unter neuem Calibrator durchgehen?"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: str
    direction: DirectionLiteral
    raw_posterior: float
    signal_posterior: float  # nach side-aware-Spiegelung, vor Calibrator
    calibrated_posterior: float  # nach Calibrator
    realized_pnl_usd: float
    would_still_trade: bool


class CounterfactualReport(BaseModel):
    """Aggregat-Bericht."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    n_trades: int
    n_would_still_trade: int
    n_would_skip: int

    pnl_realized_total_usd: float  # Σ über alle Trades (= heutiger Stand)
    pnl_realized_kept_usd: float  # Σ would_still_trade
    pnl_realized_skipped_usd: float  # Σ would_skip (positive = winners we'd lose)
    pnl_delta_usd: float  # = −pnl_realized_skipped: was wir gewinnen

    avoided_loss_count: int
    avoided_loss_sum_usd: float  # negative — Loser, die wir geblockt hätten
    skipped_gain_count: int
    skipped_gain_sum_usd: float  # positive — Winner, die wir geblockt hätten

    decision: DecisionLiteral
    decision_reasons: tuple[str, ...]
    config: CounterfactualConfig
    trades: tuple[TradeCounterfactual, ...]  # detail trail (full)


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _direction_corrected(
    posterior: float, direction: DirectionLiteral, *, side_aware: bool
) -> float:
    p = max(0.0, min(1.0, posterior))
    if side_aware and direction == "short":
        return 1.0 - p
    return p


# ─── Engine ───────────────────────────────────────────────────────────────────


def evaluate_calibrator_counterfactual(
    *,
    trade_outcomes: Sequence[TradeOutcome],
    new_calibrator: LogitLinearCalibrator | IdentityCalibrator,
    config: CounterfactualConfig | None = None,
) -> CounterfactualReport:
    """Bewerte ``new_calibrator`` gegen historische Trade-Outcomes.

    Liefert einen :class:`CounterfactualReport` mit harter Decision; raised
    nie auf degenerierten Daten.
    """
    cfg = config or CounterfactualConfig()
    n = len(trade_outcomes)

    if n < cfg.min_trades:
        return CounterfactualReport(
            n_trades=n,
            n_would_still_trade=0,
            n_would_skip=0,
            pnl_realized_total_usd=sum(t.realized_pnl_usd for t in trade_outcomes),
            pnl_realized_kept_usd=0.0,
            pnl_realized_skipped_usd=0.0,
            pnl_delta_usd=0.0,
            avoided_loss_count=0,
            avoided_loss_sum_usd=0.0,
            skipped_gain_count=0,
            skipped_gain_sum_usd=0.0,
            decision="insufficient_data",
            decision_reasons=(f"have {n} trades, need >= {cfg.min_trades}",),
            config=cfg,
            trades=(),
        )

    trades: list[TradeCounterfactual] = []
    pnl_total = 0.0
    pnl_kept = 0.0
    pnl_skipped = 0.0
    avoided_losses_sum = 0.0
    avoided_losses_n = 0
    skipped_gains_sum = 0.0
    skipped_gains_n = 0
    n_keep = 0
    n_skip = 0

    for trade in trade_outcomes:
        signal_p = _direction_corrected(
            trade.raw_posterior, trade.direction, side_aware=cfg.side_aware
        )
        calibrated_p = max(0.0, min(1.0, new_calibrator.transform(signal_p)))
        keep = calibrated_p >= cfg.threshold

        pnl_total += trade.realized_pnl_usd
        if keep:
            n_keep += 1
            pnl_kept += trade.realized_pnl_usd
        else:
            n_skip += 1
            pnl_skipped += trade.realized_pnl_usd
            if trade.realized_pnl_usd < 0:
                avoided_losses_sum += trade.realized_pnl_usd
                avoided_losses_n += 1
            elif trade.realized_pnl_usd > 0:
                skipped_gains_sum += trade.realized_pnl_usd
                skipped_gains_n += 1

        trades.append(
            TradeCounterfactual(
                decision_id=trade.decision_id,
                direction=trade.direction,
                raw_posterior=trade.raw_posterior,
                signal_posterior=signal_p,
                calibrated_posterior=calibrated_p,
                realized_pnl_usd=trade.realized_pnl_usd,
                would_still_trade=keep,
            )
        )

    pnl_delta = -pnl_skipped  # we gain by skipping losses; we lose by skipping gains

    decision_reasons: list[str] = []
    decision: DecisionLiteral
    if pnl_delta >= cfg.min_pnl_improvement_usd and pnl_delta > 0:
        decision = "approve"
        decision_reasons.append(
            f"would have saved net ${pnl_delta:+,.2f} "
            f"(avoided ${avoided_losses_sum:+,.2f} losses, "
            f"skipped ${skipped_gains_sum:+,.2f} gains across {n_skip} trades)"
        )
    elif pnl_delta < -cfg.min_pnl_improvement_usd:
        decision = "reject"
        decision_reasons.append(
            f"would have cost net ${pnl_delta:+,.2f} "
            f"(skipped ${skipped_gains_sum:+,.2f} gains, "
            f"avoided ${avoided_losses_sum:+,.2f} losses across {n_skip} trades)"
        )
    else:
        decision = "neutral"
        decision_reasons.append(
            f"net P&L delta ${pnl_delta:+,.2f} within ±"
            f"${cfg.min_pnl_improvement_usd:.2f} band — no significant edge"
        )

    if n_skip == 0:
        decision_reasons.append("calibrator would not have changed any decision")

    return CounterfactualReport(
        n_trades=n,
        n_would_still_trade=n_keep,
        n_would_skip=n_skip,
        pnl_realized_total_usd=round(pnl_total, 2),
        pnl_realized_kept_usd=round(pnl_kept, 2),
        pnl_realized_skipped_usd=round(pnl_skipped, 2),
        pnl_delta_usd=round(pnl_delta, 2),
        avoided_loss_count=avoided_losses_n,
        avoided_loss_sum_usd=round(avoided_losses_sum, 2),
        skipped_gain_count=skipped_gains_n,
        skipped_gain_sum_usd=round(skipped_gains_sum, 2),
        decision=decision,
        decision_reasons=tuple(decision_reasons),
        config=cfg,
        trades=tuple(trades),
    )


__all__ = [
    "DEFAULT_MIN_PNL_IMPROVEMENT_USD",
    "DEFAULT_MIN_TRADES",
    "DEFAULT_THRESHOLD",
    "CounterfactualConfig",
    "CounterfactualReport",
    "TradeCounterfactual",
    "TradeOutcome",
    "evaluate_calibrator_counterfactual",
]
