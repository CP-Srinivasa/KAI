"""Edge-Validation-Gate — die statistische Schranke vor echtem Kapital / Live (Plan PR C).

NUR ein read-only **Live-/Kapital-Promotion-Tor** auf der EDGE-Dimension. Es
entscheidet, ob ein Cohort einen statistisch belastbaren Edge hat, der jemals
echtes Kapital rechtfertigt — NIE eine Paper-Handelsschranke. Die Paper-Lern-
Direktive (memory feedback_paper_learning_doing_first) ist bindend: Paper handelt
und lernt OHNE dieses Gate. Hard-Invariante: dieses Modul wird NICHT vom Entry-/
Execution-Pfad importiert (Test sichert das ab).

Komplementär zu ``app/risk/promotion_gate.py`` (dem OPERATIVEN Bleed-Breaker, der
eine Promotion bei offenem Bleed/Risiko stoppt): jenes prüft die Betriebs-
sicherheit, dieses hier prüft den BEWIESENEN EDGE. Beide müssen grün sein, bevor
echtes Kapital fließt.

Es operationalisiert das 14-Punkte-Validierungs-Gate aus der Edge-Doktrin
(docs/research/edge_discovery_strategy_20260625.md) als pragmatischen,
implementierbaren Kern gegen Selektionsglück:

  1. n >= min_n (harte Stichproben-Untergrenze; Default 100).
  2. cost-net positiv (mean(net_bps) > 0) — sonst ist nichts zu promoten.
  3. Deflated Sharpe Ratio (López de Prado): PSR mit dem nach ``trials`` deflierten
     Benchmark SR0 = sqrt(Var[SR]) · ((1-γ)Φ⁻¹[1-1/N] + γΦ⁻¹[1-1/(Ne)]). Je mehr
     Hypothesen probiert, desto höher die Latte — tötet Schein-Sharpe aus
     Mehrfachtests. Verlangt DSR >= confidence (0.95).
  4. MinTRL: beobachtetes n >= minimale Track-Record-Länge für den beobachteten
     Sharpe bei ``confidence`` — die Strecke ist lang genug, um den Sharpe zu tragen.
  5. Ausreißer-robust: ohne den besten Trade bleibt mean(net_bps) > 0.
  6. Buy-&-Hold-Kontrolle (optional): schlägt der Cohort eine passive Baseline?
     Ohne Baseline ehrlich ``not_evaluated`` (advisory, kein Hard-Fail).

READY nur, wenn ALLE harten Kriterien (1–5) bestehen. Reine Berechnung, kein
Seiteneffekt.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from statistics import NormalDist
from typing import Any

_EULER_MASCHERONI = 0.5772156649015329
_E = math.e
_N01 = NormalDist(0.0, 1.0)


@dataclass(frozen=True)
class Criterion:
    """One gate sub-check: passed + the measured value vs its bar, human-readable."""

    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class EdgeValidationVerdict:
    """Outcome of the edge-validation gate. ``ready`` is the authoritative boolean."""

    ready: bool
    trade_count: int
    trials: int
    sharpe: float | None
    psr_zero: float | None
    deflated_sharpe: float | None
    min_trl: float | None
    mean_net_bps: float
    criteria: list[Criterion] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "trade_count": self.trade_count,
            "trials": self.trials,
            "sharpe": _round_opt(self.sharpe),
            "psr_zero": _round_opt(self.psr_zero),
            "deflated_sharpe": _round_opt(self.deflated_sharpe),
            "min_trl": _round_opt(self.min_trl),
            "mean_net_bps": round(self.mean_net_bps, 4),
            "criteria": [
                {"name": c.name, "passed": c.passed, "detail": c.detail} for c in self.criteria
            ],
            "note": self.note,
        }


def _round_opt(v: float | None) -> float | None:
    return None if v is None else round(v, 4)


def _moments(xs: Sequence[float]) -> tuple[float, float, float, float]:
    """(mean, std, skew, non-excess kurtosis); std uses the n-1 sample variance."""
    n = len(xs)
    mean = sum(xs) / n
    var = sum((x - mean) ** 2 for x in xs) / (n - 1)
    std = math.sqrt(var)
    if std <= 0:
        return mean, 0.0, 0.0, 3.0
    z3 = sum(((x - mean) / std) ** 3 for x in xs) / n
    z4 = sum(((x - mean) / std) ** 4 for x in xs) / n
    return mean, std, z3, z4


def _psr(sr: float, benchmark: float, n: int, skew: float, kurt: float) -> float:
    """Probabilistic Sharpe Ratio: P(true SR > benchmark) given skew/kurtosis."""
    denom = 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr * sr
    if denom <= 0 or n < 2:
        return 0.0
    z = (sr - benchmark) * math.sqrt(n - 1) / math.sqrt(denom)
    return _N01.cdf(z)


def _expected_max_sharpe_under_null(trials: int, var_sr: float) -> float:
    """SR0 = sqrt(Var[SR]) · ((1-γ)Φ⁻¹[1-1/N] + γΦ⁻¹[1-1/(Ne)]) — the Sharpe a
    USELESS strategy is expected to reach as the best of ``trials`` attempts."""
    n = max(1, trials)
    if n == 1:
        return 0.0
    a = _N01.inv_cdf(1.0 - 1.0 / n)
    b = _N01.inv_cdf(1.0 - 1.0 / (n * _E))
    return math.sqrt(max(var_sr, 0.0)) * ((1.0 - _EULER_MASCHERONI) * a + _EULER_MASCHERONI * b)


def evaluate_edge_validation(
    net_bps: Sequence[float],
    *,
    trials: int,
    min_n: int = 100,
    confidence: float = 0.95,
    benchmark_net_bps: float | None = None,
) -> EdgeValidationVerdict:
    """Decide whether a cohort's edge is statistically ready for LIVE/capital
    promotion. Read-only.

    ``net_bps`` is the per-trade cost-adjusted net edge series; ``trials`` is the
    number of distinct hypotheses/configs ever evaluated (drives DSR deflation).
    """
    n = len(net_bps)
    crits: list[Criterion] = []

    has_floor = n >= min_n
    crits.append(
        Criterion("sample_floor", has_floor, f"n={n} {'>=' if has_floor else '<'} min_n={min_n}")
    )

    if n < 2:
        crits.append(Criterion("cost_net_positive", False, "n<2: insufficient"))
        return EdgeValidationVerdict(
            ready=False,
            trade_count=n,
            trials=trials,
            sharpe=None,
            psr_zero=None,
            deflated_sharpe=None,
            min_trl=None,
            mean_net_bps=(sum(net_bps) / n if n else 0.0),
            criteria=crits,
            note="insufficient sample for any statistic",
        )

    mean, std, skew, kurt = _moments(net_bps)

    net_pos = mean > 0
    crits.append(Criterion("cost_net_positive", net_pos, f"mean_net_bps={mean:+.2f}"))

    if std <= 0 or mean <= 0:
        # No positive Sharpe → DSR/MinTRL are meaningless; fail honestly.
        crits.append(Criterion("deflated_sharpe", False, "SR<=0: no positive edge to deflate"))
        crits.append(Criterion("min_track_record", False, "SR<=0: MinTRL undefined"))
        crits.append(Criterion("outlier_robust", False, "SR<=0: not robust"))
        return EdgeValidationVerdict(
            ready=False,
            trade_count=n,
            trials=trials,
            sharpe=(mean / std if std > 0 else None),
            psr_zero=None,
            deflated_sharpe=None,
            min_trl=None,
            mean_net_bps=mean,
            criteria=crits,
            note="non-positive Sharpe — nothing to promote",
        )

    sr = mean / std
    psr0 = _psr(sr, 0.0, n, skew, kurt)
    var_sr = (1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr * sr) / (n - 1)
    sr0 = _expected_max_sharpe_under_null(trials, var_sr)
    dsr = _psr(sr, sr0, n, skew, kurt)
    dsr_ok = dsr >= confidence
    crits.append(
        Criterion(
            "deflated_sharpe",
            dsr_ok,
            f"DSR={dsr:.3f} {'>=' if dsr_ok else '<'} {confidence} "
            f"(SR={sr:.3f}, SR0={sr0:.3f}, trials={trials})",
        )
    )

    z = _N01.inv_cdf(confidence)
    denom = 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr * sr
    min_trl = denom * (z / sr) ** 2 if sr > 0 and denom > 0 else math.inf
    trl_ok = n >= min_trl
    crits.append(
        Criterion(
            "min_track_record", trl_ok, f"n={n} {'>=' if trl_ok else '<'} MinTRL={min_trl:.1f}"
        )
    )

    # Outlier-robust: drop the single best trade; the mean must stay positive.
    if n >= 3:
        trimmed = sorted(net_bps)[:-1]
        robust_mean = sum(trimmed) / len(trimmed)
        robust_ok = robust_mean > 0
    else:
        robust_mean, robust_ok = mean, False
    crits.append(Criterion("outlier_robust", robust_ok, f"mean_without_best={robust_mean:+.2f}"))

    if benchmark_net_bps is not None:
        bh_ok = mean > benchmark_net_bps
        crits.append(
            Criterion(
                "beats_buy_and_hold", bh_ok, f"{mean:+.2f} vs baseline {benchmark_net_bps:+.2f}"
            )
        )
    else:
        crits.append(
            Criterion("beats_buy_and_hold", True, "not_evaluated (no baseline) — advisory only")
        )

    hard = {
        "sample_floor",
        "cost_net_positive",
        "deflated_sharpe",
        "min_track_record",
        "outlier_robust",
    }
    ready = all(c.passed for c in crits if c.name in hard)

    return EdgeValidationVerdict(
        ready=ready,
        trade_count=n,
        trials=trials,
        sharpe=sr,
        psr_zero=psr0,
        deflated_sharpe=dsr,
        min_trl=min_trl,
        mean_net_bps=mean,
        criteria=crits,
        note="LIVE-promotion EDGE gate ONLY — never a paper-trading brake",
    )


def render_edge_validation(v: EdgeValidationVerdict) -> str:
    lines: list[str] = []
    head = "READY for live-promotion" if v.ready else "NOT ready — stays paper"
    lines.append(f"Edge-Validation: {head}  (n={v.trade_count}, trials={v.trials})")
    if v.sharpe is not None:
        lines.append(
            f"  Sharpe={v.sharpe:.3f}  PSR(0)={_fmt(v.psr_zero)}  DSR={_fmt(v.deflated_sharpe)}  "
            f"MinTRL={_fmt(v.min_trl)}  mean_net={v.mean_net_bps:+.2f}bps"
        )
    for c in v.criteria:
        mark = "PASS" if c.passed else "FAIL"
        lines.append(f"  [{mark}] {c.name}: {c.detail}")
    lines.append(f"  ({v.note})")
    return "\n".join(lines)


def _fmt(v: float | None) -> str:
    if v is None:
        return "n/a"
    if v == math.inf:
        return "inf"
    return f"{v:.3f}"
