"""Market Regime Detection Engine.

Klassifiziert eine Marktbeobachtung in eines von acht Regimen:

    BULL · BEAR · ACCUMULATION · DISTRIBUTION ·
    PANIC · EUPHORIC_BLOWOFF · LOW_LIQUIDITY · HIGH_MANIPULATION

Acht Feature-Achsen:

    volatility · correlation · orderbook_imbalance · funding_rate ·
    stablecoin_flow · whale_flow · social_momentum · macro_environment

Methoden:

    1. **Z-Score-Normalisierung** gegen optionale ``BaselineStats`` —
       Caller liefert entweder schon z-skalierte Werte oder eine Baseline.
    2. **Volatilitäts-Buckets** (low / normal / high / extreme) aus dem
       Volatility-Feature direkt.
    3. **Anomaly-Detection** über minimale Mahalanobis-Distanz zu allen
       Regime-Profilen — wenn jedes Regime weit weg ist, ist die Beobachtung
       *außerhalb* der trainierten Verteilung.
    4. **Gauß-Likelihood pro Regime** (diagonale Kovarianzen) + Prior
       → Softmax-Verteilung.
    5. **Hidden Markov Model** über eine Beobachtungs-Sequenz (Viterbi für
       den wahrscheinlichsten Pfad, Forward-Backward für die Posterior-
       Verteilung des letzten Zustands).

Ehrlich:
  - Übergangs-/Emissions-Profile sind *konfiguriert*, nicht trainiert. Das
    ist bewusst: produktiver Trainingsdatensatz fehlt; ein gut dokumentiertes
    Prior-Profil ist besser als ein schlecht trainiertes HMM.  Operator kann
    ``REGIME_PROFILES`` / ``TRANSITION_MATRIX`` mit eigener Calibration
    überschreiben.
  - K-Means / DBSCAN sind bewusst weggelassen: ohne sklearn würde eine
    eigene Implementierung Code ohne klaren Mehrwert produzieren.  Die
    Volatility-Bucket-Klassifikation deckt den 1D-Cluster-Use-Case ab.
  - Pure Python, keine neuen Dependencies.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from statistics import fmean, pstdev
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ─── Konstanten ───────────────────────────────────────────────────────────────

EPS: Final[float] = 1e-9
LOG_2PI: Final[float] = math.log(2.0 * math.pi)
ANOMALY_SCALE: Final[float] = 25.0  # Mahalanobis² ≥ 25 ≈ "deutlich außerhalb"
DEFAULT_TRANSITION_PERSISTENCE: Final[float] = 0.70  # P(state → same)


# ─── Enums ────────────────────────────────────────────────────────────────────


class MarketRegime(StrEnum):
    """Acht voneinander getrennte Regime-Klassen.

    Definition:
      - BULL: anhaltender Aufwärtstrend mit positiver Stimmung
      - BEAR: anhaltender Abwärtstrend mit negativer Stimmung
      - ACCUMULATION: leise Käufe, niedrige Vol, positives Whale-Netflow
      - DISTRIBUTION: leise Verkäufe, niedrige Vol, negatives Whale-Netflow
      - PANIC: extreme Vol-Spike, scharf negative Stimmung, Stablecoin-
        Inflows in Exchanges (Sell-Druck)
      - EUPHORIC_BLOWOFF: extreme Vol-Spike *aufwärts*, sehr hoher
        Funding-Rate, Stablecoin-Outflows, sozialer Hype
      - LOW_LIQUIDITY: dünner Markt, geringe Volumina, hohes
        Slippage-Risiko, instabiler Orderbook
      - HIGH_MANIPULATION: anomale Patterns (Vol + Spread + Wash-Trade-
        Indikatoren), schwer mit fundamentalen Daten zu reconcilen
    """

    BULL = "bull"
    BEAR = "bear"
    ACCUMULATION = "accumulation"
    DISTRIBUTION = "distribution"
    PANIC = "panic"
    EUPHORIC_BLOWOFF = "euphoric_blowoff"
    LOW_LIQUIDITY = "low_liquidity"
    HIGH_MANIPULATION = "high_manipulation_probability"


class FeatureName(StrEnum):
    """Standardisierte Feature-Namen.

    Alle Features sind als z-Scores zu liefern (oder eine Baseline mit
    ``mean`` + ``stddev`` pro Feature).  Vorzeichenkonvention pro Feature:

      - VOLATILITY: z > 0 = höher als Baseline (realized vol / IV)
      - CORRELATION: z > 0 = höher (Krisen-Korrelations-Spike)
      - ORDERBOOK_IMBALANCE: z > 0 = bid-Übergewicht, z < 0 = ask-Übergewicht
      - FUNDING_RATE: z > 0 = positive Funding (Longs überfüllt)
      - STABLECOIN_FLOW: z > 0 = Inflow zu Exchanges (bearish)
      - WHALE_FLOW: z > 0 = Whale-Akkumulation (bullish)
      - SOCIAL_MOMENTUM: z > 0 = positive soziale Stimmung / Volumen
      - MACRO_ENVIRONMENT: z > 0 = risk-on (DXY ↓, equities ↑, etc.)
    """

    VOLATILITY = "volatility"
    CORRELATION = "correlation"
    ORDERBOOK_IMBALANCE = "orderbook_imbalance"
    FUNDING_RATE = "funding_rate"
    STABLECOIN_FLOW = "stablecoin_flow"
    WHALE_FLOW = "whale_flow"
    SOCIAL_MOMENTUM = "social_momentum"
    MACRO_ENVIRONMENT = "macro_environment"


class VolatilityBucket(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    EXTREME = "extreme"


# ─── Modelle ──────────────────────────────────────────────────────────────────


class BaselineStats(BaseModel):
    """Pro-Feature mean/stddev für die z-Score-Normalisierung."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    means: dict[FeatureName, float]
    stddevs: dict[FeatureName, float]
    n_observations: int = Field(ge=0)

    @field_validator("stddevs")
    @classmethod
    def _ensure_positive_stddevs(
        cls, v: dict[FeatureName, float]
    ) -> dict[FeatureName, float]:
        for name, s in v.items():
            if s <= 0:
                raise ValueError(f"stddev for {name} must be positive (got {s})")
        return v


class RegimeObservation(BaseModel):
    """Eine einzelne Markt-Snapshot-Beobachtung über alle Features.

    Fehlende Features werden mit 0.0 (= "neutral / am Baseline-Mittel")
    aufgefüllt — explizit, damit der Caller weiß, wann er eine Lücke hat.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    timestamp_utc: datetime
    features: dict[FeatureName, float]

    @field_validator("timestamp_utc")
    @classmethod
    def _ensure_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("timestamp_utc must be timezone-aware (UTC)")
        return v.astimezone(UTC)


class FeatureContribution(BaseModel):
    """Beitrag einer Einzel-Feature zum primary_regime."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    feature: FeatureName
    z_score: float
    profile_mean: float
    deviation_sigmas: float  # |z − μ_profile| / σ_profile
    log_likelihood_contribution: float


class RegimeClassification(BaseModel):
    """Verteilung über alle acht Regime + Top-2."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    primary_regime: MarketRegime
    primary_probability: float
    secondary_regime: MarketRegime | None
    secondary_probability: float
    distribution: dict[MarketRegime, float]


class RegimeReport(BaseModel):
    """Komplettes erklärbares Engine-Output für eine Beobachtung."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    classification: RegimeClassification
    volatility_bucket: VolatilityBucket
    anomaly_score: float  # [0, 1] — 1 = klare Anomalie
    z_scores: dict[FeatureName, float]
    supporting_features: tuple[FeatureContribution, ...]
    opposing_features: tuple[FeatureContribution, ...]
    explanations: tuple[str, ...]
    residual_uncertainty_drivers: tuple[str, ...]
    hmm_path: tuple[MarketRegime, ...] | None = None  # nur bei Sequenz-Klassifikation


# ─── Numerische Hilfen ────────────────────────────────────────────────────────


def _logsumexp(values: Sequence[float]) -> float:
    if not values:
        return float("-inf")
    m = max(values)
    if m == float("-inf"):
        return float("-inf")
    return m + math.log(sum(math.exp(v - m) for v in values))


def _softmax(log_values: Mapping[MarketRegime, float]) -> dict[MarketRegime, float]:
    if not log_values:
        return {}
    lse = _logsumexp(list(log_values.values()))
    return {k: math.exp(v - lse) for k, v in log_values.items()}


def _log_gauss(z: float, mu: float, sigma: float) -> float:
    """Log-Dichte einer Normalverteilung N(mu, sigma²) am Punkt z."""
    if sigma <= 0:
        raise ValueError("sigma must be positive")
    diff = z - mu
    return -0.5 * (diff * diff) / (sigma * sigma) - math.log(sigma) - 0.5 * LOG_2PI


# ─── Regime-Profile ───────────────────────────────────────────────────────────
# Z-Score-Center pro Feature pro Regime.  Stddev pro Feature pro Regime
# legt fest, wie eng/breit die Klasse ist — schmaler = strenger.
#
# Konvention: Werte sind in *z-Score-Space* (relativ zur Baseline).
# Feature-Vorzeichen: siehe FeatureName-Docstring.

_F = FeatureName  # Kürzel
_R = MarketRegime

REGIME_PROFILES: Final[dict[MarketRegime, dict[FeatureName, tuple[float, float]]]] = {
    _R.BULL: {
        _F.VOLATILITY: (0.5, 1.0),
        _F.CORRELATION: (-0.3, 1.0),
        _F.ORDERBOOK_IMBALANCE: (0.6, 1.0),
        _F.FUNDING_RATE: (0.5, 1.0),
        _F.STABLECOIN_FLOW: (-0.5, 1.0),
        _F.WHALE_FLOW: (0.7, 1.0),
        _F.SOCIAL_MOMENTUM: (1.0, 1.0),
        _F.MACRO_ENVIRONMENT: (0.5, 1.2),
    },
    _R.BEAR: {
        _F.VOLATILITY: (0.5, 1.0),
        _F.CORRELATION: (0.5, 1.0),
        _F.ORDERBOOK_IMBALANCE: (-0.6, 1.0),
        _F.FUNDING_RATE: (-0.5, 1.0),
        _F.STABLECOIN_FLOW: (0.5, 1.0),
        _F.WHALE_FLOW: (-0.7, 1.0),
        _F.SOCIAL_MOMENTUM: (-1.0, 1.0),
        _F.MACRO_ENVIRONMENT: (-0.5, 1.2),
    },
    _R.ACCUMULATION: {
        _F.VOLATILITY: (-0.5, 0.8),
        _F.CORRELATION: (-0.5, 1.0),
        _F.ORDERBOOK_IMBALANCE: (0.3, 1.0),
        _F.FUNDING_RATE: (-0.2, 0.8),
        _F.STABLECOIN_FLOW: (-0.4, 1.0),
        _F.WHALE_FLOW: (1.5, 1.0),
        _F.SOCIAL_MOMENTUM: (-0.3, 1.0),
        _F.MACRO_ENVIRONMENT: (0.0, 1.2),
    },
    _R.DISTRIBUTION: {
        _F.VOLATILITY: (-0.4, 0.8),
        _F.CORRELATION: (0.3, 1.0),
        _F.ORDERBOOK_IMBALANCE: (-0.3, 1.0),
        _F.FUNDING_RATE: (0.5, 0.8),
        _F.STABLECOIN_FLOW: (0.4, 1.0),
        _F.WHALE_FLOW: (-1.5, 1.0),
        _F.SOCIAL_MOMENTUM: (0.4, 1.0),
        _F.MACRO_ENVIRONMENT: (0.0, 1.2),
    },
    _R.PANIC: {
        _F.VOLATILITY: (2.5, 1.0),
        _F.CORRELATION: (1.5, 0.8),
        _F.ORDERBOOK_IMBALANCE: (-1.0, 1.2),
        _F.FUNDING_RATE: (-2.0, 1.2),
        _F.STABLECOIN_FLOW: (2.0, 1.0),
        _F.WHALE_FLOW: (-1.5, 1.2),
        _F.SOCIAL_MOMENTUM: (-2.0, 1.0),
        _F.MACRO_ENVIRONMENT: (-1.0, 1.2),
    },
    _R.EUPHORIC_BLOWOFF: {
        _F.VOLATILITY: (2.0, 1.0),
        _F.CORRELATION: (-0.5, 1.2),
        _F.ORDERBOOK_IMBALANCE: (1.5, 1.2),
        _F.FUNDING_RATE: (2.5, 1.0),
        _F.STABLECOIN_FLOW: (-1.5, 1.2),
        _F.WHALE_FLOW: (-0.5, 1.2),
        _F.SOCIAL_MOMENTUM: (2.5, 1.0),
        _F.MACRO_ENVIRONMENT: (0.5, 1.2),
    },
    _R.LOW_LIQUIDITY: {
        _F.VOLATILITY: (-1.0, 0.8),
        _F.CORRELATION: (0.0, 1.5),
        _F.ORDERBOOK_IMBALANCE: (0.0, 1.5),
        _F.FUNDING_RATE: (0.0, 1.0),
        _F.STABLECOIN_FLOW: (-1.0, 1.0),
        _F.WHALE_FLOW: (-2.0, 1.2),
        _F.SOCIAL_MOMENTUM: (-1.5, 1.0),
        _F.MACRO_ENVIRONMENT: (0.0, 1.5),
    },
    _R.HIGH_MANIPULATION: {
        # Distinkte Manipulations-Marker: Bewegung *entkoppelt* vom
        # Gesamtmarkt (CORR scharf negativ) bei *extrem* einseitigem
        # Orderbook (Spoofing-Indikator).  Schmale Sigmas an diesen beiden
        # Achsen, damit das Profil sich klar gegen BULL absetzt.
        _F.VOLATILITY: (1.5, 1.0),
        _F.CORRELATION: (-1.5, 0.8),
        _F.ORDERBOOK_IMBALANCE: (2.0, 0.8),
        _F.FUNDING_RATE: (0.5, 1.5),
        _F.STABLECOIN_FLOW: (0.0, 1.5),
        _F.WHALE_FLOW: (0.5, 1.5),
        _F.SOCIAL_MOMENTUM: (1.0, 1.5),
        _F.MACRO_ENVIRONMENT: (0.0, 1.5),
    },
}

REGIME_PRIORS: Final[dict[MarketRegime, float]] = {
    _R.BULL: 0.20,
    _R.BEAR: 0.20,
    _R.ACCUMULATION: 0.15,
    _R.DISTRIBUTION: 0.15,
    _R.PANIC: 0.05,
    _R.EUPHORIC_BLOWOFF: 0.05,
    _R.LOW_LIQUIDITY: 0.10,
    _R.HIGH_MANIPULATION: 0.10,
}

# Übergangsmatrix A[i][j] = P(state_t = j | state_{t-1} = i).
# Konstruiert nach drei Prinzipien:
#   1. Persistenz: same → same = DEFAULT_TRANSITION_PERSISTENCE
#   2. Nachbarschaftslogik: bull↔accumulation/euphoric, bear↔distribution/panic, ...
#   3. Rest auf alle anderen verteilen (gleich)


def _build_transition_matrix() -> dict[MarketRegime, dict[MarketRegime, float]]:
    neighbors: dict[MarketRegime, set[MarketRegime]] = {
        _R.BULL: {_R.ACCUMULATION, _R.EUPHORIC_BLOWOFF, _R.DISTRIBUTION},
        _R.BEAR: {_R.DISTRIBUTION, _R.PANIC, _R.ACCUMULATION},
        _R.ACCUMULATION: {_R.BULL, _R.BEAR, _R.LOW_LIQUIDITY},
        _R.DISTRIBUTION: {_R.BEAR, _R.BULL, _R.EUPHORIC_BLOWOFF},
        _R.PANIC: {_R.BEAR, _R.ACCUMULATION, _R.LOW_LIQUIDITY},
        _R.EUPHORIC_BLOWOFF: {_R.DISTRIBUTION, _R.BEAR, _R.HIGH_MANIPULATION},
        _R.LOW_LIQUIDITY: {_R.ACCUMULATION, _R.HIGH_MANIPULATION},
        _R.HIGH_MANIPULATION: {_R.LOW_LIQUIDITY, _R.EUPHORIC_BLOWOFF, _R.DISTRIBUTION},
    }
    persistence = DEFAULT_TRANSITION_PERSISTENCE
    neighbor_share = 0.20  # → splittet sich auf alle Nachbarn
    rest_share = 1.0 - persistence - neighbor_share  # → auf alle nicht-Nachbarn

    matrix: dict[MarketRegime, dict[MarketRegime, float]] = {}
    all_regimes = list(_R)
    for src in all_regimes:
        row: dict[MarketRegime, float] = {}
        nbrs = neighbors[src]
        non_nbrs = [r for r in all_regimes if r != src and r not in nbrs]
        per_nbr = neighbor_share / max(len(nbrs), 1)
        per_other = rest_share / max(len(non_nbrs), 1)
        for tgt in all_regimes:
            if tgt == src:
                row[tgt] = persistence
            elif tgt in nbrs:
                row[tgt] = per_nbr
            else:
                row[tgt] = per_other
        # Sicherheits-Renormalisierung gegen Rundungsdrift
        s = sum(row.values())
        matrix[src] = {k: v / s for k, v in row.items()}
    return matrix


TRANSITION_MATRIX: Final[dict[MarketRegime, dict[MarketRegime, float]]] = (
    _build_transition_matrix()
)


# ─── Z-Scoring + Volatilitäts-Bucket + Anomaly ────────────────────────────────


def _z_score(raw: float, mean: float, stddev: float) -> float:
    if stddev <= 0:
        return 0.0
    return (raw - mean) / stddev


def _normalize_observation(
    obs: RegimeObservation, baseline: BaselineStats | None
) -> dict[FeatureName, float]:
    """Berechne z-Scores für alle Features.

    Wenn keine Baseline gegeben: Werte werden als bereits z-skaliert
    interpretiert.  Fehlende Features → 0.0 (= Baseline-Mittel).
    """
    out: dict[FeatureName, float] = dict.fromkeys(FeatureName, 0.0)
    for f in FeatureName:
        raw = obs.features.get(f)
        if raw is None:
            continue
        if baseline is None:
            out[f] = float(raw)
        else:
            mean = baseline.means.get(f, 0.0)
            sd = baseline.stddevs.get(f, 1.0)
            out[f] = _z_score(float(raw), mean, sd)
    return out


def _classify_volatility(z_vol: float) -> VolatilityBucket:
    if z_vol >= 2.5:
        return VolatilityBucket.EXTREME
    if z_vol >= 1.0:
        return VolatilityBucket.HIGH
    if z_vol <= -1.0:
        return VolatilityBucket.LOW
    return VolatilityBucket.NORMAL


def _mahalanobis_sq(
    z_scores: Mapping[FeatureName, float],
    profile: Mapping[FeatureName, tuple[float, float]],
) -> float:
    total = 0.0
    for f, (mu, sigma) in profile.items():
        z = z_scores.get(f, 0.0)
        d = (z - mu) / sigma
        total += d * d
    return total


def _anomaly_score(
    z_scores: Mapping[FeatureName, float],
    profiles: Mapping[MarketRegime, Mapping[FeatureName, tuple[float, float]]],
) -> float:
    """Ein Wert in [0, 1].  Hoch = jede Klasse weit weg → out-of-distribution."""
    if not profiles:
        return 0.0
    min_d2 = min(_mahalanobis_sq(z_scores, p) for p in profiles.values())
    return 1.0 - math.exp(-min_d2 / ANOMALY_SCALE)


# ─── Likelihood + Klassifikation ──────────────────────────────────────────────


def _log_likelihood_per_regime(
    z_scores: Mapping[FeatureName, float],
    profiles: Mapping[MarketRegime, Mapping[FeatureName, tuple[float, float]]],
    priors: Mapping[MarketRegime, float],
) -> dict[MarketRegime, float]:
    out: dict[MarketRegime, float] = {}
    for regime, profile in profiles.items():
        ll = math.log(max(priors.get(regime, EPS), EPS))
        for f, (mu, sigma) in profile.items():
            z = z_scores.get(f, 0.0)
            ll += _log_gauss(z, mu, sigma)
        out[regime] = ll
    return out


def _classify_distribution(log_lls: Mapping[MarketRegime, float]) -> RegimeClassification:
    distribution = _softmax(log_lls)
    ranked = sorted(distribution.items(), key=lambda kv: -kv[1])
    primary, primary_p = ranked[0]
    secondary, secondary_p = (ranked[1] if len(ranked) > 1 else (None, 0.0))
    return RegimeClassification(
        primary_regime=primary,
        primary_probability=round(primary_p, 6),
        secondary_regime=secondary,
        secondary_probability=round(secondary_p, 6),
        distribution={r: round(distribution.get(r, 0.0), 6) for r in MarketRegime},
    )


# ─── HMM (Viterbi + Forward) ──────────────────────────────────────────────────


def _viterbi_log(
    log_emissions: Sequence[Mapping[MarketRegime, float]],
    transitions: Mapping[MarketRegime, Mapping[MarketRegime, float]],
    initial: Mapping[MarketRegime, float],
) -> tuple[list[MarketRegime], float]:
    """Standard-Viterbi in Log-Space.

    Returns (best_path, total_log_prob).
    """
    if not log_emissions:
        return [], float("-inf")

    states = list(MarketRegime)
    log_init = {s: math.log(max(initial.get(s, EPS), EPS)) for s in states}
    log_trans = {
        s: {t: math.log(max(transitions[s].get(t, EPS), EPS)) for t in states} for s in states
    }

    delta: list[dict[MarketRegime, float]] = []
    psi: list[dict[MarketRegime, MarketRegime]] = []

    # t=0
    first = log_emissions[0]
    delta.append({s: log_init[s] + first.get(s, float("-inf")) for s in states})
    psi.append({s: s for s in states})

    for t in range(1, len(log_emissions)):
        new_delta: dict[MarketRegime, float] = {}
        new_psi: dict[MarketRegime, MarketRegime] = {}
        emit = log_emissions[t]
        for j in states:
            best_i = states[0]
            best_val = float("-inf")
            for i in states:
                v = delta[t - 1][i] + log_trans[i][j]
                if v > best_val:
                    best_val = v
                    best_i = i
            new_delta[j] = best_val + emit.get(j, float("-inf"))
            new_psi[j] = best_i
        delta.append(new_delta)
        psi.append(new_psi)

    # Backtrack
    last = delta[-1]
    final_state = max(last.items(), key=lambda kv: kv[1])[0]
    final_logp = last[final_state]
    path: list[MarketRegime] = [final_state]
    for t in range(len(log_emissions) - 1, 0, -1):
        path.append(psi[t][path[-1]])
    path.reverse()
    return path, final_logp


def _forward_log(
    log_emissions: Sequence[Mapping[MarketRegime, float]],
    transitions: Mapping[MarketRegime, Mapping[MarketRegime, float]],
    initial: Mapping[MarketRegime, float],
) -> list[dict[MarketRegime, float]]:
    """Standard-Forward in Log-Space.  Returns list of log-α_t."""
    states = list(MarketRegime)
    if not log_emissions:
        return []
    log_init = {s: math.log(max(initial.get(s, EPS), EPS)) for s in states}
    log_trans = {
        s: {t: math.log(max(transitions[s].get(t, EPS), EPS)) for t in states} for s in states
    }

    alpha: list[dict[MarketRegime, float]] = []
    first = log_emissions[0]
    alpha.append({s: log_init[s] + first.get(s, float("-inf")) for s in states})

    for t in range(1, len(log_emissions)):
        emit = log_emissions[t]
        new_alpha: dict[MarketRegime, float] = {}
        for j in states:
            new_alpha[j] = (
                _logsumexp([alpha[t - 1][i] + log_trans[i][j] for i in states])
                + emit.get(j, float("-inf"))
            )
        alpha.append(new_alpha)
    return alpha


# ─── Erklärung ────────────────────────────────────────────────────────────────


def _build_contributions(
    z_scores: Mapping[FeatureName, float],
    profile: Mapping[FeatureName, tuple[float, float]],
) -> list[FeatureContribution]:
    out: list[FeatureContribution] = []
    for f, (mu, sigma) in profile.items():
        z = z_scores.get(f, 0.0)
        ll = _log_gauss(z, mu, sigma)
        dev = abs(z - mu) / sigma if sigma > 0 else 0.0
        out.append(
            FeatureContribution(
                feature=f,
                z_score=round(z, 6),
                profile_mean=round(mu, 6),
                deviation_sigmas=round(dev, 6),
                log_likelihood_contribution=round(ll, 6),
            )
        )
    return out


def _residual_drivers(
    *,
    primary_p: float,
    secondary_p: float,
    anomaly_score: float,
    n_features_observed: int,
) -> list[str]:
    drivers: list[str] = []
    if anomaly_score >= 0.7:
        drivers.append(
            f"Hohe Anomalie (anomaly={anomaly_score:.2f}) — Beobachtung passt zu *keinem* Profil."
        )
    margin = primary_p - secondary_p
    if margin < 0.10:
        drivers.append(
            f"Knappes Rennen primary vs. secondary (Margin={margin:.2f}) — Klassifikation instabil."
        )
    if n_features_observed < 4:
        drivers.append(
            f"Wenige Features ({n_features_observed}/8) — Klassifikation auf reduziertem Vektor."
        )
    if not drivers:
        drivers.append("Keine signifikanten Restunsicherheits-Treiber identifiziert.")
    return drivers


# ─── Engine ───────────────────────────────────────────────────────────────────


class RegimeDetectionEngine:
    """Hauptklasse — zustandslos, deterministisch.

    Parameter:
      - profiles: optionale Override-Profile (sonst REGIME_PROFILES).
      - priors: optionale Override-Priors (sonst REGIME_PRIORS).
      - transitions: optionale Override-Transition-Matrix.
    """

    def __init__(
        self,
        *,
        profiles: Mapping[MarketRegime, Mapping[FeatureName, tuple[float, float]]] | None = None,
        priors: Mapping[MarketRegime, float] | None = None,
        transitions: Mapping[MarketRegime, Mapping[MarketRegime, float]] | None = None,
    ) -> None:
        self._profiles = profiles or REGIME_PROFILES
        self._priors = priors or REGIME_PRIORS
        self._transitions = transitions or TRANSITION_MATRIX
        self._validate_profiles()

    def _validate_profiles(self) -> None:
        for regime, profile in self._profiles.items():
            for f, (_mu, sigma) in profile.items():
                if sigma <= 0:
                    raise ValueError(f"profile sigma must be > 0 ({regime}/{f})")
            missing = set(FeatureName) - set(profile.keys())
            if missing:
                raise ValueError(f"profile {regime} missing features: {missing}")

    # ── Single-Snapshot ──────────────────────────────────────────────────────

    def classify(
        self,
        observation: RegimeObservation,
        *,
        baseline: BaselineStats | None = None,
    ) -> RegimeReport:
        z_scores = _normalize_observation(observation, baseline)
        log_lls = _log_likelihood_per_regime(z_scores, self._profiles, self._priors)
        classification = _classify_distribution(log_lls)

        primary_profile = self._profiles[classification.primary_regime]
        contribs = _build_contributions(z_scores, primary_profile)
        # Top-Beiträge: höchste log-likelihood = stützend, niedrigste = widersprechend
        contribs_sorted = sorted(contribs, key=lambda c: -c.log_likelihood_contribution)
        supporting = tuple(contribs_sorted[:3])
        opposing = tuple(reversed(contribs_sorted[-3:]))

        anomaly = _anomaly_score(z_scores, self._profiles)
        vol_bucket = _classify_volatility(z_scores.get(FeatureName.VOLATILITY, 0.0))

        n_obs = sum(1 for v in observation.features.values() if v is not None)
        explanations = self._build_explanations(classification, supporting, opposing, vol_bucket)
        drivers = _residual_drivers(
            primary_p=classification.primary_probability,
            secondary_p=classification.secondary_probability,
            anomaly_score=anomaly,
            n_features_observed=n_obs,
        )

        return RegimeReport(
            classification=classification,
            volatility_bucket=vol_bucket,
            anomaly_score=round(anomaly, 6),
            z_scores={f: round(z_scores.get(f, 0.0), 6) for f in FeatureName},
            supporting_features=supporting,
            opposing_features=opposing,
            explanations=tuple(explanations),
            residual_uncertainty_drivers=tuple(drivers),
            hmm_path=None,
        )

    # ── Sequence (HMM) ───────────────────────────────────────────────────────

    def classify_sequence(
        self,
        observations: Sequence[RegimeObservation],
        *,
        baseline: BaselineStats | None = None,
    ) -> RegimeReport:
        """Klassifikation über eine zeitliche Sequenz.

        Verwendet:
          - Viterbi für den global wahrscheinlichsten Pfad → ``hmm_path``
          - Forward-Algorithmus für die *Posterior-Verteilung des letzten
            Zustands* → ``classification.distribution``

        ``primary_regime`` ist der argmax der Posterior — *nicht* zwingend
        derselbe wie ``hmm_path[-1]``, weil Viterbi globale Pfad-Optimalität
        und Forward marginale Last-State-Optimalität ist.
        """
        if not observations:
            raise ValueError("observations must be non-empty")

        z_seq = [_normalize_observation(o, baseline) for o in observations]
        log_em = [
            _log_likelihood_per_regime(z, self._profiles, self._priors)
            # Strip prior; HMM hat eigenen prior (initial)
            for z in z_seq
        ]
        # Emissions: log p(o|state) — wir haben oben prior + emission gemischt.
        # Subtrahiere log prior, damit Emission rein bleibt.
        for em in log_em:
            for r in MarketRegime:
                em[r] = em[r] - math.log(max(self._priors.get(r, EPS), EPS))

        path, _ = _viterbi_log(log_em, self._transitions, self._priors)
        alpha = _forward_log(log_em, self._transitions, self._priors)
        last_alpha = alpha[-1]
        last_dist = _softmax(last_alpha)

        ranked = sorted(last_dist.items(), key=lambda kv: -kv[1])
        primary, primary_p = ranked[0]
        secondary, secondary_p = (ranked[1] if len(ranked) > 1 else (None, 0.0))
        classification = RegimeClassification(
            primary_regime=primary,
            primary_probability=round(primary_p, 6),
            secondary_regime=secondary,
            secondary_probability=round(secondary_p, 6),
            distribution={r: round(last_dist.get(r, 0.0), 6) for r in MarketRegime},
        )

        # Erklär-Layer auf der letzten Beobachtung
        last_z = z_seq[-1]
        primary_profile = self._profiles[primary]
        contribs = _build_contributions(last_z, primary_profile)
        contribs_sorted = sorted(contribs, key=lambda c: -c.log_likelihood_contribution)
        supporting = tuple(contribs_sorted[:3])
        opposing = tuple(reversed(contribs_sorted[-3:]))
        anomaly = _anomaly_score(last_z, self._profiles)
        vol_bucket = _classify_volatility(last_z.get(FeatureName.VOLATILITY, 0.0))
        n_obs = sum(1 for v in observations[-1].features.values() if v is not None)
        explanations = self._build_explanations(classification, supporting, opposing, vol_bucket)
        if path[-1] != primary:
            explanations = (
                *explanations,
                f"Viterbi-Pfad endet in {path[-1].value}, posterior bevorzugt {primary.value} — "
                "globale Pfad-Optimalität ≠ marginale Last-State-Optimalität.",
            )
        drivers = _residual_drivers(
            primary_p=classification.primary_probability,
            secondary_p=classification.secondary_probability,
            anomaly_score=anomaly,
            n_features_observed=n_obs,
        )
        return RegimeReport(
            classification=classification,
            volatility_bucket=vol_bucket,
            anomaly_score=round(anomaly, 6),
            z_scores={f: round(last_z.get(f, 0.0), 6) for f in FeatureName},
            supporting_features=supporting,
            opposing_features=opposing,
            explanations=tuple(explanations),
            residual_uncertainty_drivers=tuple(drivers),
            hmm_path=tuple(path),
        )

    # ── Baseline-Update ──────────────────────────────────────────────────────

    @staticmethod
    def update_baseline(
        observations: Sequence[RegimeObservation],
        *,
        min_observations: int = 30,
    ) -> BaselineStats:
        """Aggregiere mean/stddev pro Feature aus rohen Beobachtungen.

        Erwartet *rohe* (noch nicht z-skalierte) Werte in
        ``observation.features``.  Wirft ``ValueError``, wenn weniger als
        ``min_observations`` da sind oder ein Feature überall identisch
        ist (stddev = 0).
        """
        if len(observations) < min_observations:
            raise ValueError(
                f"need at least {min_observations} observations (got {len(observations)})"
            )
        means: dict[FeatureName, float] = {}
        stddevs: dict[FeatureName, float] = {}
        for f in FeatureName:
            values = [
                float(o.features[f])
                for o in observations
                if f in o.features and o.features[f] is not None
            ]
            if not values:
                # Feature fehlt überall → safe-Default (mean=0, stddev=1)
                means[f] = 0.0
                stddevs[f] = 1.0
                continue
            means[f] = fmean(values)
            sd = pstdev(values) if len(values) > 1 else 0.0
            if sd <= 0:
                # Verhindert Division-durch-Null beim Z-Scoring
                sd = 1.0
            stddevs[f] = sd
        return BaselineStats(means=means, stddevs=stddevs, n_observations=len(observations))

    # ── Erklärungen ──────────────────────────────────────────────────────────

    @staticmethod
    def _build_explanations(
        classification: RegimeClassification,
        supporting: Sequence[FeatureContribution],
        opposing: Sequence[FeatureContribution],
        vol_bucket: VolatilityBucket,
    ) -> list[str]:
        out: list[str] = []
        sec_value = (
            classification.secondary_regime.value if classification.secondary_regime else "n/a"
        )
        out.append(
            f"Primary: {classification.primary_regime.value} "
            f"(p={classification.primary_probability:.2f}) | "
            f"Secondary: {sec_value} "
            f"(p={classification.secondary_probability:.2f})"
        )
        if supporting:
            top = ", ".join(
                f"{c.feature.value}(z={c.z_score:+.2f}, dev={c.deviation_sigmas:.1f}σ)"
                for c in supporting[:3]
            )
            out.append(f"Stützende Features: {top}")
        if opposing:
            bot = ", ".join(
                f"{c.feature.value}(z={c.z_score:+.2f}, dev={c.deviation_sigmas:.1f}σ)"
                for c in opposing[:3]
            )
            out.append(f"Widersprechende Features: {bot}")
        out.append(f"Volatilitäts-Bucket: {vol_bucket.value}")
        return out


# ─── Convenience ──────────────────────────────────────────────────────────────


def build_default_engine() -> RegimeDetectionEngine:
    """Engine mit den ausgelieferten Profilen."""
    return RegimeDetectionEngine()


def make_observation(
    *,
    features: Mapping[FeatureName | str, float],
    timestamp_utc: datetime | None = None,
) -> RegimeObservation:
    """Bequemer Konstruktor — String-Keys werden auf FeatureName gemappt."""
    parsed: dict[FeatureName, float] = {}
    for k, v in features.items():
        key = k if isinstance(k, FeatureName) else FeatureName(k)
        parsed[key] = float(v)
    ts = timestamp_utc if timestamp_utc is not None else datetime.now(UTC)
    return RegimeObservation(timestamp_utc=ts, features=parsed)


__all__ = [
    "ANOMALY_SCALE",
    "DEFAULT_TRANSITION_PERSISTENCE",
    "REGIME_PRIORS",
    "REGIME_PROFILES",
    "TRANSITION_MATRIX",
    "BaselineStats",
    "FeatureContribution",
    "FeatureName",
    "MarketRegime",
    "RegimeClassification",
    "RegimeDetectionEngine",
    "RegimeObservation",
    "RegimeReport",
    "VolatilityBucket",
    "build_default_engine",
    "make_observation",
]
