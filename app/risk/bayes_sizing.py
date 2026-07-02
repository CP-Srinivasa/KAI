"""Bayes-aware Position-Sizing — Fractional Kelly + Risk-Adjustments.

Pflicht aus dem KAI-Leitsatz "Kapital intelligent verteilen": die
Position-Größe ist eine Entscheidung *unter Unsicherheit*, kein
Reflex einer Vorhersage.

Modell
------

  Kelly-Fraktion (raw):
      f* = (p · b − (1 − p)) / b
      p  = win-Wahrscheinlichkeit (aus posterior_probability + Direction)
      b  = Reward/Risk-Ratio (Take-Profit-Distanz / Stop-Distanz, in Vielfachen)

  Adjusted:
      f_adj = max(0, f*) · κ · (1 − u) · (1 − a) · c

      κ = kelly_fraction      ∈ (0, 1]   — Default 0.25 ("quarter Kelly")
      u = bayes_uncertainty   ∈ [0, 1]
      a = regime_anomaly      ∈ [0, 1]
      c = bayes_confidence    ∈ [0, 1]   — zusätzlicher Konfidenz-Multiplikator

  Caps:
      f_cap = min(f_adj, max_risk_per_trade_fraction, drawdown_remaining_fraction)

Vertrag
-------
  - Negative Edges (f* ≤ 0) → ``position_fraction = 0`` mit klarem ``capped_by="negative_edge"``.
  - ``equity ≤ 0`` oder ``stop_loss_pct ≤ 0`` → harte Ablehnung.
  - Drawdown-Kill: wenn ``drawdown_remaining_pct ≤ 0`` → 0 mit ``"drawdown_exhausted"``.
  - Alle Multiplier + Caps werden im Output aufgelistet — Audit-Pflicht.
  - Pure Python.
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_KELLY_FRACTION: Final[float] = 0.25  # quarter Kelly
ABSOLUTE_FRACTION_HARD_CEILING: Final[float] = 0.10  # nie > 10 % equity, egal was Kelly sagt


class BayesSizingInput(BaseModel):
    """Eingabe-Bündel für den Sizer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    win_probability: float = Field(ge=0.0, le=1.0)  # bereits richtungsbewusst
    expected_reward_pct: float = Field(gt=0.0)  # erwarteter Gewinn (% vom entry)
    stop_loss_pct: float = Field(gt=0.0)  # Stop-Distanz (% vom entry)
    bayes_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    bayes_uncertainty: float = Field(default=0.0, ge=0.0, le=1.0)
    regime_anomaly: float = Field(default=0.0, ge=0.0, le=1.0)
    equity: float = Field(gt=0.0)
    max_risk_per_trade_pct: float = Field(default=0.25, gt=0.0, le=100.0)
    drawdown_remaining_pct: float = Field(default=100.0, ge=-1.0, le=100.0)
    kelly_fraction: float = Field(default=DEFAULT_KELLY_FRACTION, gt=0.0, le=1.0)


class AppliedMultiplier(BaseModel):
    """Ein einzelner Multiplikator + Begründung im Audit-Output."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    factor: float
    note: str


class BayesSizingDecision(BaseModel):
    """Sizing-Entscheidung — vollständig erklärbar."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    approved: bool
    position_fraction: float  # Anteil der equity in dieser Position [0, 1]
    position_size_usd: float
    kelly_raw: float  # f* vor Multiplikatoren
    kelly_after_adjustments: float  # nach κ + Multipliern, vor Caps
    multipliers: tuple[AppliedMultiplier, ...]
    # None | "max_risk_per_trade" | "drawdown" | "ceiling"
    # | "negative_edge" | "invalid_input" | "drawdown_exhausted"
    capped_by: str | None
    rationale: str


# ─── Kern ─────────────────────────────────────────────────────────────────────


def _kelly_raw(win_p: float, reward: float, risk: float) -> float:
    """Standard-Kelly: f* = (p·b − q) / b mit b = reward/risk."""
    if risk <= 0:
        return 0.0
    b = reward / risk
    if b <= 0:
        return 0.0
    return (win_p * b - (1.0 - win_p)) / b


def compute_bayes_sized_position(inp: BayesSizingInput) -> BayesSizingDecision:
    """Berechne die Sizing-Entscheidung deterministisch + erklärbar."""

    if inp.equity <= 0 or inp.stop_loss_pct <= 0:
        return BayesSizingDecision(
            approved=False,
            position_fraction=0.0,
            position_size_usd=0.0,
            kelly_raw=0.0,
            kelly_after_adjustments=0.0,
            multipliers=(),
            capped_by="invalid_input",
            rationale="equity oder stop_loss_pct ≤ 0 — keine Sizing-Entscheidung möglich.",
        )

    if inp.drawdown_remaining_pct <= 0.0:
        return BayesSizingDecision(
            approved=False,
            position_fraction=0.0,
            position_size_usd=0.0,
            kelly_raw=0.0,
            kelly_after_adjustments=0.0,
            multipliers=(),
            capped_by="drawdown_exhausted",
            rationale=(
                f"Drawdown-Budget erschöpft "
                f"(remaining={inp.drawdown_remaining_pct:.2f}%) — Trade abgelehnt."
            ),
        )

    f_raw = _kelly_raw(inp.win_probability, inp.expected_reward_pct, inp.stop_loss_pct)

    if f_raw <= 0:
        return BayesSizingDecision(
            approved=False,
            position_fraction=0.0,
            position_size_usd=0.0,
            kelly_raw=round(f_raw, 6),
            kelly_after_adjustments=0.0,
            multipliers=(),
            capped_by="negative_edge",
            rationale=(
                f"Kelly-Edge nicht-positiv (p={inp.win_probability:.3f}, "
                f"reward/risk={inp.expected_reward_pct / inp.stop_loss_pct:.2f}) — "
                "Trade abgelehnt."
            ),
        )

    multipliers = [
        AppliedMultiplier(
            name="kelly_fraction",
            factor=inp.kelly_fraction,
            note=f"Operator-Default {DEFAULT_KELLY_FRACTION} (quarter Kelly).",
        ),
        AppliedMultiplier(
            name="bayes_confidence",
            factor=inp.bayes_confidence,
            note="Bayes-Confidence-Score skaliert die Position direkt.",
        ),
        AppliedMultiplier(
            name="one_minus_uncertainty",
            factor=1.0 - inp.bayes_uncertainty,
            note=f"Bayes-Uncertainty {inp.bayes_uncertainty:.3f} dämpft Position.",
        ),
        AppliedMultiplier(
            name="one_minus_anomaly",
            factor=1.0 - inp.regime_anomaly,
            note=f"Regime-Anomaly {inp.regime_anomaly:.3f} dämpft Position.",
        ),
    ]

    f_adj = f_raw
    for m in multipliers:
        f_adj *= m.factor

    f_adj = max(0.0, f_adj)

    # Caps
    capped_by: str | None = None
    max_per_trade = inp.max_risk_per_trade_pct / 100.0
    drawdown_remaining = inp.drawdown_remaining_pct / 100.0
    f_cap = min(f_adj, max_per_trade, drawdown_remaining, ABSOLUTE_FRACTION_HARD_CEILING)
    if f_cap < f_adj:
        # Welcher Cap zuerst gegriffen?
        if max_per_trade <= drawdown_remaining and max_per_trade <= ABSOLUTE_FRACTION_HARD_CEILING:
            capped_by = "max_risk_per_trade"
        elif drawdown_remaining <= ABSOLUTE_FRACTION_HARD_CEILING:
            capped_by = "drawdown"
        else:
            capped_by = "ceiling"

    position_size_usd = f_cap * inp.equity
    rationale = (
        f"Kelly raw={f_raw:.4f} → adjusted={f_adj:.4f} → final={f_cap:.4f} "
        f"({position_size_usd:,.2f} USD bei equity={inp.equity:,.2f}). "
        f"win_p={inp.win_probability:.3f}, reward/risk="
        f"{inp.expected_reward_pct / inp.stop_loss_pct:.2f}, "
        f"confidence={inp.bayes_confidence:.3f}, uncertainty={inp.bayes_uncertainty:.3f}, "
        f"anomaly={inp.regime_anomaly:.3f}."
    )

    return BayesSizingDecision(
        approved=f_cap > 0,
        position_fraction=round(f_cap, 6),
        position_size_usd=round(position_size_usd, 2),
        kelly_raw=round(f_raw, 6),
        kelly_after_adjustments=round(f_adj, 6),
        multipliers=tuple(multipliers),
        capped_by=capped_by,
        rationale=rationale,
    )


__all__ = [
    "ABSOLUTE_FRACTION_HARD_CEILING",
    "DEFAULT_KELLY_FRACTION",
    "AppliedMultiplier",
    "BayesSizingDecision",
    "BayesSizingInput",
    "compute_bayes_sized_position",
]
