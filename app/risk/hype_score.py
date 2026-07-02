"""Hype-Score-Core (HYPE-S1) — Sentiment-Überhitzung als messbarer Skalar.

Leitsatz des Moduls: *ein starkes Asset bekommt nicht automatisch ein
Buy-Signal.* Wenn ein Asset medial überhitzt, ist die Aufmerksamkeits-Crowd
bereits positioniert — neue Long-Einstiege kaufen dann den Hype, nicht die
These. Dieses Modul macht „überhitzt" deterministisch messbar, aus Strömen,
die KAI BEREITS besitzt (analysierte Dokumente mit Sentiment + Asset-Tags).
Keine neue externe Datenquelle, keine Vorhersage — eine Risiko-Messgröße
mit ausgewiesenen Komponenten.

Modell (rein, deterministisch, erklärbar)
=========================================

Pro Asset über ein jüngstes Fenster W (default 6 h) gegen die EIGENE
Baseline B (default 7 d, auf W normiert):

  velocity_component   v = clamp( (m/b − 1) / (S_v − 1), 0, 1 )
      m = Mentions in W, b = erwartete Mentions pro W aus der Baseline,
      S_v = velocity_saturation (default 5× Baseline ⇒ 1.0).
      Floor: m < min_mentions ⇒ Score 0 („insufficient data", 2 statt 1
      Erwähnung ist Rauschen, kein Hype). Kalte Baseline (b ≈ 0) wird auf
      mindestens 1 Mention/W geklemmt — ein frisch gelistetes Asset kann
      dadurch hohe Velocity zeigen, aber nie durch 0 teilen.

  breadth_component    β = clamp( distinct_sources / S_b, 0, 1 )
      S_b = breadth_saturation (default 5 Quellen ⇒ 1.0). Ein einzelner
      Kanal, der spammt, ist KEINE Breite.

  one_sidedness        ω = |bullish − bearish| / max(bullish + bearish, 1)
      Nur direktionale Mentions zählen; alles-bullish ⇒ 1.0.

  hype_score = v · (0.4 + 0.3·β + 0.3·ω)   ∈ [0, 1]

Begründung der Form: abnormale Velocity ist die NOTWENDIGE Bedingung
(ohne sie Score 0 — viel Breite über normalem Newsfluss ist Berichterstattung,
kein Hype). Breite + Einseitigkeit verstärken: ein schmaler Spike aus einer
Quelle sättigt bei 0.4, erst breit + einseitig + schnell erreicht 1.0.

Vertrag
=======
  - Pure Python, kein I/O, kein Netz, keine Uhr (Zeit kommt vom Caller).
  - Alle Komponenten werden im Ergebnis ausgewiesen — Audit-Pflicht.
  - Verwendet wird der Score als contrarian-Evidence
    (``build_sentiment_overheat_evidence``) und im Hype-Snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

#: Gewichts-Split des Amplifikations-Faktors: Basis + Breite + Einseitigkeit.
AMPLIFIER_BASE: Final[float] = 0.4
AMPLIFIER_BREADTH_WEIGHT: Final[float] = 0.3
AMPLIFIER_ONE_SIDEDNESS_WEIGHT: Final[float] = 0.3


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


class HypeScoreConfig(BaseModel):
    """Scoring-Parameter — Defaults spiegeln ``HypeEvidenceSettings``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    recent_window_hours: float = Field(default=6.0, gt=0.0)
    baseline_days: float = Field(default=7.0, gt=0.0)
    min_mentions: int = Field(default=5, ge=1)
    velocity_saturation: float = Field(default=5.0, gt=1.0)
    breadth_saturation: int = Field(default=5, ge=1)


class HypeInputs(BaseModel):
    """Aggregierte Beobachtungen EINES Assets (vom Aggregator geliefert)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    asset: str = Field(min_length=1)
    mentions_recent: int = Field(ge=0)
    mentions_baseline_total: int = Field(ge=0)  # Mentions im gesamten Baseline-Zeitraum
    distinct_sources_recent: int = Field(ge=0)
    bullish_recent: int = Field(ge=0)
    bearish_recent: int = Field(ge=0)


class HypeScoreResult(BaseModel):
    """Vollständig erklärbarer Score — jede Komponente ausgewiesen."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    asset: str
    score: float  # ∈ [0, 1]
    velocity_component: float
    breadth_component: float
    one_sidedness: float
    velocity_ratio: float  # m/b roh (vor Saturierung), für Audit/Report
    baseline_per_window: float
    insufficient_data: bool
    rationale: str


def compute_hype_score(inputs: HypeInputs, config: HypeScoreConfig) -> HypeScoreResult:
    """Berechne den Hype-Score deterministisch + erklärbar (pure function)."""
    windows_in_baseline = max((config.baseline_days * 24.0) / config.recent_window_hours, 1.0)
    # Kalte Baseline (neues Asset / dünne Historie) ⇒ mindestens 1 Mention/W
    # als Nenner: hohe Velocity bleibt möglich, Division durch ~0 nicht.
    baseline_per_window = max(inputs.mentions_baseline_total / windows_in_baseline, 1.0)

    if inputs.mentions_recent < config.min_mentions:
        return HypeScoreResult(
            asset=inputs.asset,
            score=0.0,
            velocity_component=0.0,
            breadth_component=0.0,
            one_sidedness=0.0,
            velocity_ratio=inputs.mentions_recent / baseline_per_window,
            baseline_per_window=round(baseline_per_window, 4),
            insufficient_data=True,
            rationale=(
                f"{inputs.asset}: nur {inputs.mentions_recent} Mentions in "
                f"{config.recent_window_hours:.0f}h (< min {config.min_mentions}) — "
                "kein Score auf dünnen Daten (fail-safe)."
            ),
        )

    velocity_ratio = inputs.mentions_recent / baseline_per_window
    velocity = _clamp01((velocity_ratio - 1.0) / (config.velocity_saturation - 1.0))
    breadth = _clamp01(inputs.distinct_sources_recent / float(config.breadth_saturation))
    directional = inputs.bullish_recent + inputs.bearish_recent
    one_sidedness = (
        abs(inputs.bullish_recent - inputs.bearish_recent) / directional if directional > 0 else 0.0
    )

    amplifier = (
        AMPLIFIER_BASE
        + AMPLIFIER_BREADTH_WEIGHT * breadth
        + AMPLIFIER_ONE_SIDEDNESS_WEIGHT * one_sidedness
    )
    score = _clamp01(velocity * amplifier)

    rationale = (
        f"{inputs.asset}: {inputs.mentions_recent} Mentions/"
        f"{config.recent_window_hours:.0f}h = {velocity_ratio:.1f}× Baseline "
        f"({baseline_per_window:.1f}/Fenster) → velocity={velocity:.2f}; "
        f"{inputs.distinct_sources_recent} Quellen → breadth={breadth:.2f}; "
        f"bull/bear={inputs.bullish_recent}/{inputs.bearish_recent} → "
        f"one_sidedness={one_sidedness:.2f}; score={score:.3f}."
    )
    return HypeScoreResult(
        asset=inputs.asset,
        score=round(score, 6),
        velocity_component=round(velocity, 6),
        breadth_component=round(breadth, 6),
        one_sidedness=round(one_sidedness, 6),
        velocity_ratio=round(velocity_ratio, 4),
        baseline_per_window=round(baseline_per_window, 4),
        insufficient_data=False,
        rationale=rationale,
    )


# ─── Aggregation (pure; DB-Query bleibt im Refresh-Service) ──────────────────


@dataclass(frozen=True)
class DocMention:
    """Leichtgewichtige Sicht auf EIN analysiertes Dokument (eine Zeile der
    Refresh-Query). ``assets`` = ``crypto_assets``-Tags, ``sentiment_label``
    = klassifiziertes Label (bullish/bearish/neutral/None)."""

    observed_at: datetime
    source_name: str | None
    sentiment_label: str | None
    assets: tuple[str, ...]


_BULLISH_LABELS: Final[frozenset[str]] = frozenset({"bullish", "positive"})
_BEARISH_LABELS: Final[frozenset[str]] = frozenset({"bearish", "negative"})


def aggregate_hype_inputs(
    mentions: list[DocMention],
    *,
    now: datetime,
    config: HypeScoreConfig,
) -> dict[str, HypeInputs]:
    """Aggregiere Dokument-Mentions zu per-Asset ``HypeInputs`` (pure).

    Asset-Schlüssel = Base-Asset, upper-cased: Paar-Tags wie ``BTC/USDT``
    (so liefert sie die ``tickers``-Spalte) werden auf ``BTC`` normalisiert,
    damit ``BTC`` und ``BTC/USDT`` in EINEN Bucket fallen. Pro Dokument zählt
    jedes Asset höchstens einmal (Doppel-Tagging ``BTC`` + ``BTC/USDT`` ist
    EINE Mention). Dokumente außerhalb des Baseline-Zeitraums werden
    ignoriert; tz-naive Timestamps werden übersprungen (kein stilles
    Fehl-Bucketing).
    """
    recent_cutoff = now - timedelta(hours=config.recent_window_hours)
    baseline_cutoff = now - timedelta(days=config.baseline_days, hours=config.recent_window_hours)

    recent_count: dict[str, int] = {}
    baseline_count: dict[str, int] = {}
    sources: dict[str, set[str]] = {}
    bullish: dict[str, int] = {}
    bearish: dict[str, int] = {}

    for mention in mentions:
        ts = mention.observed_at
        if ts.tzinfo is None or ts < baseline_cutoff or ts > now:
            continue
        is_recent = ts >= recent_cutoff
        label = (mention.sentiment_label or "").strip().lower()
        seen_assets: set[str] = set()
        for raw_asset in mention.assets:
            asset = raw_asset.strip().upper().split("/", 1)[0]
            if not asset or asset in seen_assets:
                continue
            seen_assets.add(asset)
            if is_recent:
                recent_count[asset] = recent_count.get(asset, 0) + 1
                if mention.source_name:
                    sources.setdefault(asset, set()).add(mention.source_name.strip().lower())
                if label in _BULLISH_LABELS:
                    bullish[asset] = bullish.get(asset, 0) + 1
                elif label in _BEARISH_LABELS:
                    bearish[asset] = bearish.get(asset, 0) + 1
            else:
                baseline_count[asset] = baseline_count.get(asset, 0) + 1

    out: dict[str, HypeInputs] = {}
    for asset, m_recent in recent_count.items():
        out[asset] = HypeInputs(
            asset=asset,
            mentions_recent=m_recent,
            mentions_baseline_total=baseline_count.get(asset, 0),
            distinct_sources_recent=len(sources.get(asset, set())),
            bullish_recent=bullish.get(asset, 0),
            bearish_recent=bearish.get(asset, 0),
        )
    return out


__all__ = [
    "AMPLIFIER_BASE",
    "AMPLIFIER_BREADTH_WEIGHT",
    "AMPLIFIER_ONE_SIDEDNESS_WEIGHT",
    "DocMention",
    "HypeInputs",
    "HypeScoreConfig",
    "HypeScoreResult",
    "aggregate_hype_inputs",
    "compute_hype_score",
]
