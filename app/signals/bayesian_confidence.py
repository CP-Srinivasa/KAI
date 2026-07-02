"""Bayesian Signal Confidence Engine.

Pro Signal werden Wahrscheinlichkeit, Unsicherheit und Evidenz-Gewicht
*probabilistisch* aus heterogenen Beobachtungen abgeleitet — News-Relevanz,
historische Trefferquote, On-Chain-Daten, Volumen-Reaktion, Funding-Rate,
Open Interest, Liquidationen, Marktregime und Quellenvertrauen.

Mathematik (Log-Odds Bayes, deterministisch):

    Prior:
        π₀  = clamp( w_h · h + (1 − w_h) · π_base ,  ε, 1−ε )
        w_h = min(1, n_obs / N_FULL_TRUST)

    Pro Evidence i (Kind, value ∈ [−1, 1], direction_aligned ∈ {−1, 0, +1},
    source_trust ∈ [0, 1], freshness ∈ [0, 1]):
        ℓᵢ              = calibrate(kindᵢ, valueᵢ, direction_alignedᵢ)
        contributionᵢ   = ℓᵢ · source_trustᵢ · freshnessᵢ
        logit(π_post)   = logit(π₀) + Σ contributionᵢ

    Aggregat:
        evidence_weight = Σ |contributionᵢ|                      (ℝ⁺)
        agreement       = |Σ contributionᵢ| / max(evidence_weight, ε)
        certainty       = √( agreement · tanh(evidence_weight / W_SCALE) )
        uncertainty     = 1 − certainty
        confidence      = certainty · |2·π_post − 1|

Erklärbarkeit ist kein Anhängsel: jede Evidence wird einzeln gewichtet,
bewertet und im `ConfidenceReport` als `EvidenceContribution` ausgewiesen —
explizit getrennt nach „erhöht / reduziert / neutral / verworfen".

Kein starres Sentiment-System. Keine Hidden-Defaults. Calibrators sind
ausgewiesen, getestbar und pro Kind dokumentiert.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ─── Numerische Konstanten ────────────────────────────────────────────────────

EPS: Final[float] = 1e-6
L_MAX: Final[float] = 2.0  # max |log-likelihood-ratio| pro Einzelevidence
W_SCALE: Final[float] = 4.0  # Skala für Sättigung der evidence-mass → certainty
N_FULL_TRUST: Final[int] = 30  # ab so vielen historischen Beobachtungen Vollvertrauen in hit_rate
DEFAULT_FRESHNESS_HALFLIFE_S: Final[float] = 6 * 3600.0  # 6h, evidence-decay
PRIOR_BASE: Final[float] = 0.5  # uninformative Bernoulli-Prior


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _logit(p: float) -> float:
    p = _clamp(p, EPS, 1.0 - EPS)
    return math.log(p / (1.0 - p))


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


# ─── Domain-Enums ─────────────────────────────────────────────────────────────


class EvidenceKind(StrEnum):
    """Semantische Kategorie einer einzelnen Beobachtung.

    Jede Kategorie hat einen eigenen Calibrator (siehe `_CALIBRATORS`) und
    einen kategorialen Skalierungsfaktor — dadurch sind die Beiträge nicht
    austauschbar: ein 0.8-On-Chain-Wert wirkt anders als 0.8-Funding-Rate.
    """

    NEWS_RELEVANCE = "news_relevance"
    HISTORICAL_HIT_RATE = "historical_hit_rate"
    ON_CHAIN = "on_chain"
    VOLUME_REACTION = "volume_reaction"
    FUNDING_RATE = "funding_rate"
    OPEN_INTEREST = "open_interest"
    LONG_SHORT_RATIO = "long_short_ratio"
    SENTIMENT_OVERHEAT = "sentiment_overheat"
    LIQUIDATIONS = "liquidations"
    L2_ONCHAIN = "l2_onchain"  # Sprint 2: fee/mempool flow, direction-agnostic (B-003)
    MOMENTUM = "momentum"  # G3: own-data universe momentum, direction-agnostic until learned
    MARKET_REGIME = "market_regime"
    SOURCE_TRUST = "source_trust"  # nur als Modulator; eigener Update-Beitrag = 0


class ContributionEffect(StrEnum):
    INCREASED = "increased"  # Wahrscheinlichkeit erhöht
    DECREASED = "decreased"  # Wahrscheinlichkeit reduziert
    NEUTRAL = "neutral"  # Beitrag unter Rauschschwelle
    DISCARDED = "discarded"  # Evidence verworfen (z. B. value außerhalb erlaubter Bereich)


# Kategoriale Stärke-Modulatoren — bewusst kein gleichmäßiges Gewicht.
# Begründung pro Kind im Calibrator-Block dokumentiert.
_KIND_STRENGTH: Final[Mapping[EvidenceKind, float]] = {
    EvidenceKind.NEWS_RELEVANCE: 1.0,
    EvidenceKind.HISTORICAL_HIT_RATE: 0.6,
    EvidenceKind.ON_CHAIN: 1.2,
    EvidenceKind.VOLUME_REACTION: 1.0,
    EvidenceKind.FUNDING_RATE: 0.8,
    EvidenceKind.OPEN_INTEREST: 0.7,
    EvidenceKind.LONG_SHORT_RATIO: 0.6,
    EvidenceKind.SENTIMENT_OVERHEAT: 0.6,
    EvidenceKind.LIQUIDATIONS: 1.1,
    EvidenceKind.L2_ONCHAIN: 0.7,  # conservative — shadow-first, edge-gated trust
    EvidenceKind.MOMENTUM: 0.7,  # conservative — shadow-first, edge-gated trust (G3)
    EvidenceKind.MARKET_REGIME: 0.5,
    EvidenceKind.SOURCE_TRUST: 0.0,
}

_NEUTRAL_THRESHOLD: Final[float] = 0.05  # |contribution| ≤ → "neutral" im Report


# ─── Pydantic-Modelle ─────────────────────────────────────────────────────────


class Evidence(BaseModel):
    """Eine einzelne, typisierte Beobachtung.

    Felder:
      - kind: EvidenceKind, bestimmt Calibrator + Stärke-Modulator.
      - value: signierte Stärke ∈ [−1, +1].  +1 = maximal pro Signalrichtung,
        −1 = maximal contra. Für Quellen, die nur Magnitude liefern, ist
        ``direction_aligned`` zu setzen und ``value`` als ≥ 0 zu übergeben.
      - direction_aligned: −1 = contra-Signal, +1 = pro-Signal, 0 = neutral.
        Sinnvoll, wenn die Rohdaten richtungsneutral sind (z. B. Volumen).
      - source_trust ∈ [0, 1]: Quellenvertrauen (Multiplikator auf Beitrag).
      - observed_at: Zeitpunkt der Beobachtung (für Freshness-Decay).
      - half_life_s: optionaler eigener Decay (sonst Engine-Default).
      - source_id / note: Audit-Felder, kein Einfluss auf Mathematik.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: EvidenceKind
    value: float = Field(ge=-1.0, le=1.0)
    direction_aligned: int = Field(default=1, ge=-1, le=1)
    source_trust: float = Field(default=1.0, ge=0.0, le=1.0)
    observed_at: datetime | None = None
    half_life_s: float | None = Field(default=None, gt=0.0)
    source_id: str | None = None
    note: str | None = None

    @field_validator("observed_at")
    @classmethod
    def _ensure_utc(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return None
        if v.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware (UTC)")
        return v.astimezone(UTC)


class EvidenceContribution(BaseModel):
    """Aufgeschlüsselter Beitrag *einer* Evidence zur Posterior-Berechnung."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: EvidenceKind
    source_id: str | None
    raw_value: float
    direction_aligned: int
    log_likelihood_ratio: float
    source_trust: float
    freshness: float
    contribution: float  # = LLR · source_trust · freshness
    effect: ContributionEffect
    note: str | None = None


class ConfidenceReport(BaseModel):
    """Vollständig erklärbarer Output der Engine.

    Felder:
      - prior_probability:   Bayes-Prior π₀ ∈ (ε, 1−ε)
      - posterior_probability: π_post = σ( logit(π₀) + Σ contributions )
      - confidence_score:    [0, 1] — kombiniert Richtungs-Schärfe und
                              Evidenz-Konsens.  Hoch *nur* wenn π_post weit
                              von 0.5 *und* Evidenz reichlich + konsistent.
      - uncertainty_score:   1 − sqrt(agreement · tanh(W / W_SCALE)).
      - evidence_weight:     Σ |contribution| (rohe Informationsmasse).
      - increased / decreased / neutral / discarded:  sortierte
                              EvidenceContribution-Listen für Audit + UI.
      - residual_uncertainty_drivers:  menschenlesbare Kurzbegründungen,
                              warum Restunsicherheit verbleibt.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    prior_probability: float
    posterior_probability: float
    confidence_score: float
    uncertainty_score: float
    evidence_weight: float
    agreement: float
    increased: tuple[EvidenceContribution, ...]
    decreased: tuple[EvidenceContribution, ...]
    neutral: tuple[EvidenceContribution, ...]
    discarded: tuple[EvidenceContribution, ...]
    residual_uncertainty_drivers: tuple[str, ...]


# ─── Calibrators ──────────────────────────────────────────────────────────────
# Jeder Calibrator bekommt einen *signierten* Wert ∈ [−1, +1] und liefert
# einen Log-Likelihood-Ratio in [−L_MAX, +L_MAX].  Der kategoriale
# Stärke-Modulator (`_KIND_STRENGTH`) wird *zentral* in `_calibrate` appliziert
# — jeder einzelne Calibrator bleibt auf seine Mapping-Logik beschränkt.


def _calibrate_linear(value: float) -> float:
    """Lineare 1:1-Abbildung von signiertem Wert → LLR.

    Genutzt für Kinds, deren Rohinput bereits direkt auf
    Wahrscheinlichkeitsverschiebung mappt (NEWS, ON_CHAIN, VOLUME, OI, LIQ).
    """
    return _clamp(value, -1.0, 1.0) * L_MAX


def _calibrate_funding(value: float) -> float:
    """Funding-Rate-Mapping mit semantischer Inversionsbehandlung.

    Erwartung: Caller hat ``direction_aligned`` so gesetzt, dass ein
    *positiver* ``value`` bereits "contrarian-warning" bedeutet (siehe
    ``build_funding_rate_evidence``).  Hier nur Sättigung, damit extreme
    Funding-Spitzen nicht doppelt durchschlagen.
    """
    return math.tanh(_clamp(value, -1.0, 1.0) * 1.5) * L_MAX


def _calibrate_long_short(value: float) -> float:
    """Long/Short-Account-Ratio-Mapping (contrarian crowded-trade).

    Erwartung: Caller hat ``direction_aligned`` so gesetzt, dass ein
    *positiver* ``value`` bereits "crowd ist auf der Signalseite überfüllt →
    contra" bedeutet (siehe ``build_long_short_ratio_evidence``). Hier nur
    Sättigung — wie ``_calibrate_funding``, damit ein extrem überfülltes
    Buch (ratio nahe 0/1) nicht überproportional durchschlägt. Gleiche
    tanh-Form wie Funding, weil beide Crowd-Positionierungs-Contrarians sind.
    """
    return math.tanh(_clamp(value, -1.0, 1.0) * 1.5) * L_MAX


def _calibrate_sentiment_overheat(value: float) -> float:
    """Sentiment-Überhitzungs-Mapping (contrarian crowd-attention).

    Erwartung: Caller (``build_sentiment_overheat_evidence``) hat
    ``direction_aligned`` bereits so gesetzt, dass Überhitzung contra zur
    Signalrichtung kodiert ist. Hier nur Sättigung — gleiche tanh-Form wie
    Funding/L/S, weil Hype derselben contrarian-Familie angehört (überfüllte
    Aufmerksamkeit statt überfülltes Orderbuch) und extreme Score-Spitzen
    nicht überproportional durchschlagen dürfen.
    """
    return math.tanh(_clamp(value, -1.0, 1.0) * 1.5) * L_MAX


def _calibrate_regime(value: float) -> float:
    """Marktregime-Modulator — bewusst gedämpft.

    +1 = Regime stützt Signal-Direction (Trend in Signalrichtung).
    −1 = Regime widerspricht (Range, oder Trend gegen Signal).
    Weiche tanh-Sättigung, da Regime-Klassifikation selbst unsicher ist.
    """
    return math.tanh(_clamp(value, -1.0, 1.0)) * L_MAX * 0.7


def _calibrate_hit_rate(value: float) -> float:
    """Historische Trefferquote — fließt primär in den *Prior* ein.

    Wird sie zusätzlich als Evidence übergeben, wirkt sie schwach
    (Doppelzählungsschutz).  Konvex gedämpft.
    """
    v = _clamp(value, -1.0, 1.0)
    return math.copysign(v * v, v) * L_MAX * 0.5


def _calibrate_zero(_: float) -> float:
    """SOURCE_TRUST liefert keinen direkten Update-Beitrag."""
    return 0.0


_CALIBRATORS: Final[Mapping[EvidenceKind, Callable[[float], float]]] = {
    EvidenceKind.NEWS_RELEVANCE: _calibrate_linear,
    EvidenceKind.HISTORICAL_HIT_RATE: _calibrate_hit_rate,
    EvidenceKind.ON_CHAIN: _calibrate_linear,
    EvidenceKind.VOLUME_REACTION: _calibrate_linear,
    EvidenceKind.FUNDING_RATE: _calibrate_funding,
    EvidenceKind.OPEN_INTEREST: _calibrate_linear,
    EvidenceKind.LONG_SHORT_RATIO: _calibrate_long_short,
    EvidenceKind.SENTIMENT_OVERHEAT: _calibrate_sentiment_overheat,
    EvidenceKind.LIQUIDATIONS: _calibrate_linear,
    # L2 on-chain: neutral saturation; the direction lives ONLY in
    # direction_aligned (data-driven, B-003) — never baked into the calibrator.
    EvidenceKind.L2_ONCHAIN: _calibrate_linear,
    # Momentum (G3): neutral saturation; direction lives ONLY in direction_aligned
    # (learned by scripts/evaluate_momentum_evidence.py) — never baked in here.
    EvidenceKind.MOMENTUM: _calibrate_linear,
    EvidenceKind.MARKET_REGIME: _calibrate_regime,
    EvidenceKind.SOURCE_TRUST: _calibrate_zero,
}


def _calibrate(kind: EvidenceKind, value: float, direction_aligned: int) -> float:
    """Signed-LLR mit Direction-Alignment + kategorialem Modulator."""
    base = _CALIBRATORS[kind](value)
    # direction_aligned == 0 → kein direktionaler Beitrag.
    return base * direction_aligned * _KIND_STRENGTH[kind]


def _freshness(observed_at: datetime | None, *, now: datetime, half_life_s: float) -> float:
    """Exponentieller Decay; ohne Timestamp = 1.0 (volles Gewicht)."""
    if observed_at is None:
        return 1.0
    age = (now - observed_at).total_seconds()
    if age <= 0:
        return 1.0
    return math.pow(0.5, age / half_life_s)


# ─── Engine ───────────────────────────────────────────────────────────────────


class BayesianConfidenceEngine:
    """Probabilistische Engine für Signal-Confidence + Erklärbarkeit.

    Reine, zustandslose Berechnung. Wiederverwendbar pro Signal.

    Parameter:
      - prior_base: Bernoulli-Basis-Prior, default 0.5 (uninformativ).
      - n_full_trust: ab wievielen historischen Beobachtungen ``hit_rate``
        in den Prior dominiert.  Verhindert, dass eine 100 %-Quote aus 1
        Sample den Prior auf 0.99 zwingt.
      - default_half_life_s: Decay-Halbwertszeit, falls Evidence keinen
        eigenen Wert mitbringt.
    """

    def __init__(
        self,
        *,
        prior_base: float = PRIOR_BASE,
        n_full_trust: int = N_FULL_TRUST,
        default_half_life_s: float = DEFAULT_FRESHNESS_HALFLIFE_S,
    ) -> None:
        if not 0.0 < prior_base < 1.0:
            raise ValueError("prior_base must be in (0, 1)")
        if n_full_trust <= 0:
            raise ValueError("n_full_trust must be positive")
        if default_half_life_s <= 0:
            raise ValueError("default_half_life_s must be positive")
        self._prior_base = prior_base
        self._n_full_trust = n_full_trust
        self._default_half_life_s = default_half_life_s

    # ── Prior ─────────────────────────────────────────────────────────────────

    def compute_prior(
        self,
        *,
        historical_hit_rate: float | None = None,
        n_observations: int = 0,
        source_trust: float = 1.0,
    ) -> float:
        """Beta-artiger Mix-Prior aus historischer Trefferquote + Quellenvertrauen.

        Wenn ``historical_hit_rate`` fehlt oder ``n_observations == 0``,
        kollabiert der Prior auf ``prior_base``.  ``source_trust`` dämpft
        zusätzlich das Vertrauen in die hit_rate-Stichprobe — eine 80 %
        Trefferquote aus einer Quelle mit Trust 0.2 bewegt den Prior
        kaum.
        """
        if historical_hit_rate is None or n_observations <= 0:
            return _clamp(self._prior_base, EPS, 1.0 - EPS)

        h = _clamp(historical_hit_rate, 0.0, 1.0)
        trust = _clamp(source_trust, 0.0, 1.0)
        evidence_weight = min(1.0, n_observations / float(self._n_full_trust)) * trust
        prior = evidence_weight * h + (1.0 - evidence_weight) * self._prior_base
        return _clamp(prior, EPS, 1.0 - EPS)

    # ── Update ────────────────────────────────────────────────────────────────

    def evaluate(
        self,
        evidences: Sequence[Evidence],
        *,
        prior_probability: float | None = None,
        historical_hit_rate: float | None = None,
        n_observations: int = 0,
        source_trust: float = 1.0,
        now: datetime | None = None,
    ) -> ConfidenceReport:
        """Vollständige Bayes-Auswertung → ConfidenceReport.

        ``prior_probability`` hat Vorrang vor (``historical_hit_rate``,
        ``n_observations``).  Werden weder explizit noch implizit
        Prior-Inputs geliefert, fällt der Prior auf ``prior_base``.
        """
        if now is None:
            now = datetime.now(UTC)
        if now.tzinfo is None:
            raise ValueError("'now' must be timezone-aware (UTC)")

        if prior_probability is not None:
            prior = _clamp(prior_probability, EPS, 1.0 - EPS)
        else:
            prior = self.compute_prior(
                historical_hit_rate=historical_hit_rate,
                n_observations=n_observations,
                source_trust=source_trust,
            )

        contributions: list[EvidenceContribution] = []
        prior_logit = _logit(prior)
        posterior_logit = prior_logit

        for ev in evidences:
            half_life = ev.half_life_s if ev.half_life_s is not None else self._default_half_life_s
            freshness = _freshness(ev.observed_at, now=now, half_life_s=half_life)
            llr = _calibrate(ev.kind, ev.value, ev.direction_aligned)
            contribution_value = llr * ev.source_trust * freshness
            posterior_logit += contribution_value

            effect = self._classify_effect(contribution_value, ev)
            contributions.append(
                EvidenceContribution(
                    kind=ev.kind,
                    source_id=ev.source_id,
                    raw_value=ev.value,
                    direction_aligned=ev.direction_aligned,
                    log_likelihood_ratio=llr,
                    source_trust=ev.source_trust,
                    freshness=freshness,
                    contribution=contribution_value,
                    effect=effect,
                    note=ev.note,
                )
            )

        posterior = _sigmoid(posterior_logit)

        # Aggregate: |Σ| / Σ|·| = "wie konsistent zogen die Evidences in eine Richtung"
        active = [
            c
            for c in contributions
            if c.effect in (ContributionEffect.INCREASED, ContributionEffect.DECREASED)
        ]
        total_abs = sum(abs(c.contribution) for c in active)
        net = sum(c.contribution for c in active)
        agreement = abs(net) / total_abs if total_abs > EPS else 0.0
        evidence_mass = math.tanh(total_abs / W_SCALE)
        certainty = math.sqrt(max(0.0, agreement * evidence_mass))
        uncertainty = 1.0 - certainty
        directional_strength = abs(2.0 * posterior - 1.0)
        confidence = directional_strength * certainty

        increased = tuple(
            sorted(
                (c for c in contributions if c.effect == ContributionEffect.INCREASED),
                key=lambda c: -c.contribution,
            )
        )
        decreased = tuple(
            sorted(
                (c for c in contributions if c.effect == ContributionEffect.DECREASED),
                key=lambda c: c.contribution,
            )
        )
        neutral = tuple(c for c in contributions if c.effect == ContributionEffect.NEUTRAL)
        discarded = tuple(c for c in contributions if c.effect == ContributionEffect.DISCARDED)

        return ConfidenceReport(
            prior_probability=round(prior, 6),
            posterior_probability=round(posterior, 6),
            confidence_score=round(_clamp(confidence, 0.0, 1.0), 6),
            uncertainty_score=round(_clamp(uncertainty, 0.0, 1.0), 6),
            evidence_weight=round(total_abs, 6),
            agreement=round(agreement, 6),
            increased=increased,
            decreased=decreased,
            neutral=neutral,
            discarded=discarded,
            residual_uncertainty_drivers=self._residual_drivers(
                evidences=evidences,
                evidence_mass=evidence_mass,
                agreement=agreement,
                contributions=contributions,
            ),
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _classify_effect(contribution: float, ev: Evidence) -> ContributionEffect:
        if ev.direction_aligned == 0 or ev.source_trust <= 0.0:
            return ContributionEffect.DISCARDED
        if abs(contribution) <= _NEUTRAL_THRESHOLD:
            return ContributionEffect.NEUTRAL
        return ContributionEffect.INCREASED if contribution > 0 else ContributionEffect.DECREASED

    @staticmethod
    def _residual_drivers(
        *,
        evidences: Sequence[Evidence],
        evidence_mass: float,
        agreement: float,
        contributions: Sequence[EvidenceContribution],
    ) -> tuple[str, ...]:
        drivers: list[str] = []

        if not evidences:
            drivers.append(
                "Keine Evidenz vorhanden — Posterior = Prior, Confidence rein direktional."
            )
            return tuple(drivers)

        if evidence_mass < 0.25:
            drivers.append(
                f"Geringe Evidenzmasse (mass={evidence_mass:.2f}) — "
                "wenige oder schwache Beobachtungen."
            )
        # agreement == 0.0 ist der härteste Konflikt (Σ contributions = 0 trotz Masse).
        active_count = sum(
            1
            for c in contributions
            if c.effect in (ContributionEffect.INCREASED, ContributionEffect.DECREASED)
        )
        if active_count >= 2 and agreement < 0.6:
            drivers.append(
                f"Konflikt zwischen Evidenzen (agreement={agreement:.2f}) — "
                "pro/contra heben sich teilweise auf."
            )

        low_trust = [
            c
            for c in contributions
            if c.source_trust < 0.4 and c.effect != ContributionEffect.DISCARDED
        ]
        if low_trust:
            kinds = ", ".join(sorted({c.kind.value for c in low_trust}))
            drivers.append(f"Niedrigvertrauens-Quellen tragen bei: {kinds}.")

        stale = [
            c
            for c in contributions
            if c.freshness < 0.5 and c.effect != ContributionEffect.DISCARDED
        ]
        if stale:
            kinds = ", ".join(sorted({c.kind.value for c in stale}))
            drivers.append(f"Veraltete Beobachtungen (freshness < 0.5): {kinds}.")

        discarded_kinds = sorted(
            {c.kind.value for c in contributions if c.effect == ContributionEffect.DISCARDED}
        )
        if discarded_kinds:
            drivers.append(
                "Verworfen (direction_aligned=0 oder source_trust=0): "
                f"{', '.join(discarded_kinds)}."
            )

        if not drivers:
            drivers.append("Keine signifikanten Restunsicherheits-Treiber identifiziert.")
        return tuple(drivers)


# ─── Evidence-Factories (semantische Korrektheit kapseln) ─────────────────────
# Diese Helfer sind *die* empfohlene Schnittstelle für Caller — sie sorgen
# dafür, dass Funding-Rate-Inversion, Volumen-Direction-Alignment etc. nicht
# bei jedem Call aufs Neue richtig gesetzt werden müssen.


def build_news_evidence(
    *,
    relevance: float,
    sentiment_aligned_with_signal: bool,
    source_trust: float = 1.0,
    observed_at: datetime | None = None,
    source_id: str | None = None,
) -> Evidence:
    """News-Beitrag.  ``relevance`` ∈ [0, 1] mappt auf signed value ∈ [−1, +1]
    je nach Sentiment-Alignment mit der Signalrichtung.
    """
    rel = _clamp(relevance, 0.0, 1.0)
    aligned = 1 if sentiment_aligned_with_signal else -1
    return Evidence(
        kind=EvidenceKind.NEWS_RELEVANCE,
        value=rel,
        direction_aligned=aligned,
        source_trust=source_trust,
        observed_at=observed_at,
        source_id=source_id,
        note=f"news relevance={rel:.2f} aligned={sentiment_aligned_with_signal}",
    )


def build_on_chain_evidence(
    *,
    netflow_zscore: float,
    inflow_to_exchange: bool,
    signal_is_long: bool,
    source_trust: float = 1.0,
    observed_at: datetime | None = None,
    source_id: str | None = None,
) -> Evidence:
    """On-Chain-Netflow → Evidence.

    Inflow-zu-Exchange = bearish (Verkaufsdruck).  Outflow = bullish (HODL-Drift).
    Wird gegen Signalrichtung gespiegelt.
    """
    z = _clamp(netflow_zscore / 3.0, -1.0, 1.0)  # 3σ-Sättigung
    raw_bullish = -1 if inflow_to_exchange else +1
    aligned = raw_bullish * (1 if signal_is_long else -1)
    return Evidence(
        kind=EvidenceKind.ON_CHAIN,
        value=abs(z),
        direction_aligned=aligned,
        source_trust=source_trust,
        observed_at=observed_at,
        source_id=source_id,
        note=f"netflow_z={netflow_zscore:.2f} inflow={inflow_to_exchange}",
    )


def build_volume_evidence(
    *,
    volume_zscore: float,
    price_move_aligned_with_signal: bool,
    source_trust: float = 1.0,
    observed_at: datetime | None = None,
    source_id: str | None = None,
) -> Evidence:
    """Volumen-Reaktion.  Hohe Volume bei aligned-Price-Move = Bestätigung.
    Hohe Volume bei contra-Move = Distribution/Akkumulation gegen Signal.
    """
    z = _clamp(volume_zscore / 3.0, -1.0, 1.0)
    aligned = 1 if price_move_aligned_with_signal else -1
    return Evidence(
        kind=EvidenceKind.VOLUME_REACTION,
        value=abs(z),
        direction_aligned=aligned,
        source_trust=source_trust,
        observed_at=observed_at,
        source_id=source_id,
        note=f"vol_z={volume_zscore:.2f} aligned_move={price_move_aligned_with_signal}",
    )


def build_funding_rate_evidence(
    *,
    funding_rate_pct: float,
    signal_is_long: bool,
    source_trust: float = 1.0,
    observed_at: datetime | None = None,
    source_id: str | None = None,
) -> Evidence:
    """Funding-Rate (perpetuals).

    Konvention: positive Funding-Rate ⇒ Longs zahlen Shorts ⇒ überfüllte
    Long-Seite ⇒ contrarian-warning *für* LONG-Signale, pro für SHORT.
    """
    fr = _clamp(funding_rate_pct / 0.05, -1.0, 1.0)  # 5 bp Sättigung (8h-Funding)
    sign = -1 if signal_is_long else +1  # invertiert für Long
    aligned = sign * (1 if fr > 0 else -1 if fr < 0 else 0)
    return Evidence(
        kind=EvidenceKind.FUNDING_RATE,
        value=abs(fr),
        direction_aligned=aligned,
        source_trust=source_trust,
        observed_at=observed_at,
        source_id=source_id,
        note=f"funding={funding_rate_pct:.4f}%/8h signal_long={signal_is_long}",
    )


def build_l2_onchain_evidence(
    *,
    fee_percentile: float | None,
    mempool_percentile: float | None,
    direction_aligned: int = 0,
    source_trust: float = 1.0,
    observed_at: datetime | None = None,
    source_id: str | None = None,
) -> Evidence:
    """L2 on-chain flow (fee/mempool) → Evidence. **DIRECTION-AGNOSTIC (B-003).**

    Deliberately UNLIKE :func:`build_funding_rate_evidence`: there is NO
    ``signal_is_long`` here. The magnitude is the on-chain *extremity* — how far the
    current fee / mempool percentiles sit from their median (0.5) — while the
    direction is supplied by the caller via ``direction_aligned`` (learned from
    evaluation, never hardcoded contrarian/pro-trend). v1 passes
    ``direction_aligned=0`` (undetermined) → zero contribution, so this is a pure
    shadow measurement until ``scripts/evaluate_l2_evidence.py`` learns a sign.

    A ``None`` percentile (no window yet) is treated as the median → 0 extremity,
    i.e. "nothing to say", never a fabricated signal.
    """
    fee_dev = abs((fee_percentile if fee_percentile is not None else 0.5) - 0.5)
    mempool_dev = abs((mempool_percentile if mempool_percentile is not None else 0.5) - 0.5)
    value = _clamp(fee_dev + mempool_dev, 0.0, 1.0)  # 0..1 on-chain extremity
    return Evidence(
        kind=EvidenceKind.L2_ONCHAIN,
        value=value,
        direction_aligned=direction_aligned,
        source_trust=source_trust,
        observed_at=observed_at,
        source_id=source_id,
        note=f"fee_pct={fee_percentile} mempool_pct={mempool_percentile} dir={direction_aligned}",
    )


def build_momentum_evidence(
    *,
    momentum_score: float | None,
    direction_aligned: int = 0,
    source_trust: float = 1.0,
    observed_at: datetime | None = None,
    source_id: str | None = None,
) -> Evidence:
    """Own-data universe momentum → Evidence. **DIRECTION-AGNOSTIC (G3).**

    Like :func:`build_l2_onchain_evidence`: the magnitude is the momentum
    *extremity* — how far the symbol's momentum percentile sits from the neutral
    median (0.5), scaled to ``[0, 1]`` — while the direction is supplied by the
    caller via ``direction_aligned`` (learned by
    ``scripts/evaluate_momentum_evidence.py``, never hardcoded pro-/contra-trend).
    v1 passes ``direction_aligned=0`` (undetermined) → zero contribution, so this
    is a pure shadow measurement until the operator promotes a learned sign on
    proof. A ``None`` score (symbol not in the universe yet) is treated as the
    median → 0 extremity ("nothing to say"), never a fabricated signal.
    """
    score = momentum_score if momentum_score is not None else 0.5
    value = _clamp(abs(score - 0.5) * 2.0, 0.0, 1.0)  # 0..1 momentum extremity from neutral
    return Evidence(
        kind=EvidenceKind.MOMENTUM,
        value=value,
        direction_aligned=direction_aligned,
        source_trust=source_trust,
        observed_at=observed_at,
        source_id=source_id,
        note=f"momentum_score={momentum_score} dir={direction_aligned}",
    )


def build_open_interest_evidence(
    *,
    oi_change_zscore: float,
    price_move_aligned_with_signal: bool,
    source_trust: float = 1.0,
    observed_at: datetime | None = None,
    source_id: str | None = None,
) -> Evidence:
    """Open-Interest-Änderung.

    OI ↑ + Price aligned = neue Positionen bestätigen Signal.
    OI ↑ + Price contra  = neue Positionen *gegen* Signal — bearish/contra.
    OI ↓                 = Position-Closing, schwächt Signal beidseitig.
    """
    z = _clamp(oi_change_zscore / 3.0, -1.0, 1.0)
    if z >= 0:
        aligned = 1 if price_move_aligned_with_signal else -1
        magnitude = abs(z)
    else:
        # OI fällt → Markt verlässt Positionen → schwache Bestätigung beidseitig
        aligned = -1
        magnitude = abs(z) * 0.5
    return Evidence(
        kind=EvidenceKind.OPEN_INTEREST,
        value=magnitude,
        direction_aligned=aligned,
        source_trust=source_trust,
        observed_at=observed_at,
        source_id=source_id,
        note=f"oi_dz={oi_change_zscore:.2f} aligned_move={price_move_aligned_with_signal}",
    )


def build_long_short_ratio_evidence(
    *,
    long_account_ratio: float,
    signal_is_long: bool,
    source_trust: float = 1.0,
    observed_at: datetime | None = None,
    source_id: str | None = None,
) -> Evidence:
    """Long/Short-Account-Ratio (perpetuals) — contrarian crowded-trade.

    ``long_account_ratio`` ∈ [0, 1] ist der Anteil der Accounts, die long
    positioniert sind (Binance ``longAccount`` / Bybit ``buyRatio`` — beide
    bereits Anteil, KEINE Prozent-Skalierung).

    Semantik (contrarian, spiegelt ``build_funding_rate_evidence``):
      - ratio > 0.5  ⇒ Buch ist *long-überfüllt* ⇒ Long-Squeeze-Risiko ⇒
        SHORT-Evidence (contra zu einem LONG-Signal, pro für SHORT).
      - ratio < 0.5  ⇒ Buch ist *short-überfüllt* ⇒ Short-Squeeze-Fuel ⇒
        LONG-Evidence (pro für LONG, contra für SHORT).
      - Mittelfeld (0.45–0.55) ⇒ neutral ⇒ ``value ≈ 0`` (Deadzone, keine
        richtungslose Mikro-Evidence aus Rauschen).

    Magnitude skaliert mit dem Abstand von 0.5: |ratio − 0.5| / 0.5 ∈ [0, 1]
    (ratio 0 oder 1 ⇒ Magnitude 1.0). Die Deadzone wird *vor* der Skalierung
    abgezogen, sodass z. B. ratio 0.60 (Operator-Schwelle „crowded") bereits
    eine klare, aber nicht extreme Evidence ergibt.

    ``direction_aligned`` kodiert — wie bei Funding — bereits die contrarian-
    Inversion, der Calibrator (``_calibrate_long_short``) sieht nur eine
    nicht-negative Magnitude und sättigt sie.
    """
    r = _clamp(long_account_ratio, 0.0, 1.0)
    deviation = r - 0.5  # >0 = long-crowded, <0 = short-crowded
    deadzone = 0.05  # |ratio−0.5| ≤ 0.05 (0.45–0.55) → neutral
    if abs(deviation) <= deadzone:
        magnitude = 0.0
        crowd_sign = 0  # neutral → no direction
    else:
        # Restabstand nach Deadzone, normiert auf den verbleibenden Bereich
        # [0.05, 0.5] → [0, 1]. ratio 0/1 → 1.0.
        magnitude = _clamp((abs(deviation) - deadzone) / (0.5 - deadzone), 0.0, 1.0)
        crowd_sign = 1 if deviation > 0 else -1  # +1 = long-crowded, -1 = short-crowded
    # Contrarian-Inversion: long-crowded (crowd_sign=+1) ist contra für LONG.
    # invert = -1 für LONG, +1 für SHORT (identisch zur Funding-Konvention).
    invert = -1 if signal_is_long else +1
    aligned = invert * crowd_sign
    return Evidence(
        kind=EvidenceKind.LONG_SHORT_RATIO,
        value=magnitude,
        direction_aligned=aligned,
        source_trust=source_trust,
        observed_at=observed_at,
        source_id=source_id,
        note=f"ls_ratio={r:.3f} signal_long={signal_is_long}",
    )


def build_sentiment_overheat_evidence(
    *,
    hype_score: float,
    signal_is_long: bool,
    dampen_only: bool = True,
    source_trust: float = 1.0,
    observed_at: datetime | None = None,
    source_id: str | None = None,
) -> Evidence:
    """Sentiment-Überhitzung (Hype) — contrarian crowd-attention (HYPE-S1).

    ``hype_score`` ∈ [0, 1] kommt aus ``app.risk.hype_score`` (abnormale
    Mention-Velocity × Quellen-Breite × Sentiment-Einseitigkeit). Hoher Score
    heißt: das Asset ist medial überhitzt — die Aufmerksamkeits-Crowd ist
    bereits eingestiegen. Das ist eine WARNUNG gegen neue Long-Einstiege,
    keine Bestätigung („gutes Unternehmen ≠ guter Einstieg").

    Semantik:
      - LONG-Signal + Überhitzung ⇒ contra-Evidence (``direction_aligned=-1``)
        — der Posterior sinkt, Kelly-Sizing schrumpft, ggf. kein Signal.
      - SHORT-Signal: mit ``dampen_only=True`` (S1-Default) wird KEINE
        Evidence-Richtung gesetzt (``direction_aligned=0`` ⇒ Engine
        verwirft den Beitrag, bleibt aber im Audit sichtbar). Hype darf
        Positionen nur dämpfen, nie neue (Short-)Positionen begründen.
        ``dampen_only=False`` aktiviert die symmetrische contrarian-Lesart
        (pro-Short, analog Funding/L/S) — bewusste SPÄTER-Entscheidung.
    """
    score = _clamp(hype_score, 0.0, 1.0)
    if signal_is_long:
        aligned = -1 if score > 0 else 0
    elif dampen_only:
        aligned = 0
    else:
        aligned = 1 if score > 0 else 0
    return Evidence(
        kind=EvidenceKind.SENTIMENT_OVERHEAT,
        value=score,
        direction_aligned=aligned,
        source_trust=source_trust,
        observed_at=observed_at,
        source_id=source_id,
        note=(f"hype_score={score:.3f} signal_long={signal_is_long} dampen_only={dampen_only}"),
    )


def build_liquidations_evidence(
    *,
    liquidation_volume_usd: float,
    contra_side_dominant: bool,
    signal_is_long: bool,
    source_trust: float = 1.0,
    observed_at: datetime | None = None,
    source_id: str | None = None,
    saturation_usd: float = 50_000_000.0,
) -> Evidence:
    """Liquidations-Cascades.

    ``contra_side_dominant=True`` heißt: die *gegenüberliegende* Seite des
    Signals wird liquidiert — das ist Treibstoff für die Signalrichtung.
    Magnitude über tanh-Sättigung (Default: 50M$ ≈ 1.0).
    """
    mag = math.tanh(max(0.0, liquidation_volume_usd) / max(saturation_usd, 1.0))
    aligned = 1 if contra_side_dominant else -1
    return Evidence(
        kind=EvidenceKind.LIQUIDATIONS,
        value=mag,
        direction_aligned=aligned,
        source_trust=source_trust,
        observed_at=observed_at,
        source_id=source_id,
        note=(
            f"liq_usd={liquidation_volume_usd:,.0f} contra_side_dom={contra_side_dominant} "
            f"signal_long={signal_is_long}"
        ),
    )


def build_market_regime_evidence(
    *,
    regime: str,  # "trending_with" | "trending_against" | "ranging" | "volatile" | "unknown"
    source_trust: float = 1.0,
    observed_at: datetime | None = None,
    source_id: str | None = None,
) -> Evidence:
    """Marktregime → Modulator.  ``regime`` muss bereits relativ zur
    Signalrichtung klassifiziert sein.
    """
    mapping = {
        "trending_with": (1.0, 1),
        "trending_against": (1.0, -1),
        "ranging": (0.4, -1),  # Range dämpft Trend-Signale
        "volatile": (0.6, -1),  # Volatilität erhöht Stop-Risiko → contra-Punkt
        "unknown": (0.0, 0),
    }
    if regime not in mapping:
        raise ValueError(f"Unknown regime: {regime!r}")
    val, aligned = mapping[regime]
    return Evidence(
        kind=EvidenceKind.MARKET_REGIME,
        value=val,
        direction_aligned=aligned,
        source_trust=source_trust,
        observed_at=observed_at,
        source_id=source_id,
        note=f"regime={regime}",
    )


def build_historical_hit_rate_evidence(
    *,
    hit_rate: float,
    n_observations: int,
    source_trust: float = 1.0,
    source_id: str | None = None,
) -> Evidence:
    """Historische Trefferquote als (schwache) Evidence.

    Empfohlen: hit_rate primär in `compute_prior`/`evaluate(historical_hit_rate=…)`
    übergeben.  Diese Factory ist für den Fall, dass Caller sie *zusätzlich*
    als Bestätigungs-Evidence kennzeichnen will (deutlich gedämpft).
    """
    centered = _clamp((hit_rate - 0.5) * 2.0, -1.0, 1.0)
    sample_weight = min(1.0, n_observations / float(N_FULL_TRUST))
    return Evidence(
        kind=EvidenceKind.HISTORICAL_HIT_RATE,
        value=centered * sample_weight,
        direction_aligned=1,
        source_trust=source_trust,
        source_id=source_id,
        note=f"hit_rate={hit_rate:.2f} n={n_observations}",
    )


# ─── Convenience ──────────────────────────────────────────────────────────────


def build_default_engine() -> BayesianConfidenceEngine:
    """Engine mit KAI-Defaults (PRIOR_BASE=0.5, 30er-Vollvertrauen, 6h Halbwertszeit)."""
    return BayesianConfidenceEngine()


__all__ = [
    "BayesianConfidenceEngine",
    "ConfidenceReport",
    "ContributionEffect",
    "DEFAULT_FRESHNESS_HALFLIFE_S",
    "Evidence",
    "EvidenceContribution",
    "EvidenceKind",
    "L_MAX",
    "N_FULL_TRUST",
    "PRIOR_BASE",
    "W_SCALE",
    "build_default_engine",
    "build_funding_rate_evidence",
    "build_historical_hit_rate_evidence",
    "build_l2_onchain_evidence",
    "build_liquidations_evidence",
    "build_long_short_ratio_evidence",
    "build_market_regime_evidence",
    "build_news_evidence",
    "build_on_chain_evidence",
    "build_open_interest_evidence",
    "build_sentiment_overheat_evidence",
    "build_volume_evidence",
]
