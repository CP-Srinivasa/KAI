"""Priority scoring for AnalysisResult.

Computes a single priority integer (1–10) from the scored fields
of an AnalysisResult. Used to rank documents for alerts and research packs.

Formula (weighted sum → mapped to [1, 10]):
  relevance  × 0.30   — is this even about our topics?
  impact     × 0.30   — what's the potential market effect?
  novelty    × 0.20   — is this new information?
  actionable × 0.15   — does it require a decision?
  (1-spam)   × 0.05   — quality signal

Actionability bonus: +1 to final priority if result.actionable is True.
Spam penalty: if spam_probability > 0.7 → priority capped at 3.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.domain.document import AnalysisResult

# Score weights (must sum to 1.0)
_W_RELEVANCE: float = 0.30
_W_IMPACT: float = 0.30
_W_NOVELTY: float = 0.20
_W_ACTIONABLE: float = 0.15
_W_QUALITY: float = 0.05

_SPAM_CAP_THRESHOLD: float = 0.70
_SPAM_PRIORITY_CAP: int = 3

# --- Alert-Gate auf der KONTINUIERLICHEN Groesse -----------------------------
#
# ``priority`` entsteht aus ``round(raw * 9) + 1``. Eine Ganzzahl-Schwelle ist
# damit in Wahrheit eine Schwelle auf ``raw``: ``priority >= p`` gilt genau dann,
# wenn ``raw >= (p - 1.5) / 9``. Fuer p=7 ist das 0.61111.
#
# Diese Kante liegt zufaellig zwischen zwei tatsaechlich vorkommenden raw-Werten.
# Gemessen an 460 echten Entscheidungen (230 Dokumente, zwei Modelle) liegen die
# raw-Werte in Schritten von 0.005 — das LLM liefert seine Teil-Scores in
# 0,1-Schritten, die gewichtete Summe erzeugt daraus ein grobes Raster. Direkt
# an der Kante:
#
#     0.61000   1 Dokument   0.00111 UNTER der Kante  -> kein Alert
#     0.61500   2 Dokumente  0.00389 UEBER der Kante  -> Alert
#
# 2,0 % aller Entscheidungen kippen bei einer Stoerung von nur +-0.005, 9,6 % bei
# +-0.020. Eine Groesse, die das Modell in 0,1-Schritten schaetzt, entscheidet
# also ueber Abstaende von 0,005.
#
# ``ALERT_GATE_RAW`` macht die Schwelle explizit und legt sie auf 0.615: dieselbe
# Dokumentmenge wie heute (91/91 im Messsatz, null zusaetzlich, null verloren),
# aber die Kipp-Zone schrumpft von 9 auf 7 Entscheidungen. Die gerundete
# ``priority`` bleibt unveraendert — sie ist weiterhin die Zahl fuer Anzeige und
# Sortierung, nur nicht mehr die Entscheidungsgrundlage.
ALERT_GATE_RAW: float = 0.615


# ── Regelpfad-Deckel (Budget-Policy v2, 2026-09-10) ──────────────────────────
#
# Diese drei Obergrenzen leben im Fallback in ``app/analysis/pipeline.py`` und
# stehen HIER, weil sie nur zusammen mit den Gewichten oben eine Aussage ergeben.
# Zwei Orte waeren zwei Meinungen darueber, wie hoch der Regelpfad kommt.
#
#: ``_fallback_relevance`` endet auf ``min(1.0, ...)``.
RULE_RELEVANCE_CEILING: float = 1.00
#: ``_fallback_impact`` endet auf ``min(0.35, ...)``.
RULE_IMPACT_CEILING: float = 0.35
#: ``_fallback_novelty`` gibt hoechstens 0.6 zurueck (Dokument juenger als 24 h).
RULE_NOVELTY_CEILING: float = 0.60


def rule_path_raw_ceiling() -> float:
    """Der hoechste ``raw``-Wert, den der Regelpfad ueberhaupt erreichen kann.

    Aus den Gewichten und den Fallback-Obergrenzen gerechnet, nicht notiert:
    aendert jemand ein Gewicht oder einen Deckel, wandert diese Zahl mit.

    ``actionable`` ist im Regelpfad dauerhaft ``False`` (I-13) — das ist keine
    Schaetzung, sondern eine Invariante, die ``_build_fallback_analysis``
    ausdruecklich einhaelt. Damit faellt ``_W_ACTIONABLE`` vollstaendig weg.
    ``quality`` erreicht 1.0 bei ``spam_probability == 0``.
    """
    return (
        RULE_RELEVANCE_CEILING * _W_RELEVANCE
        + RULE_IMPACT_CEILING * _W_IMPACT
        + RULE_NOVELTY_CEILING * _W_NOVELTY
        + 0.0 * _W_ACTIONABLE
        + 1.0 * _W_QUALITY
    )


def rule_path_priority_ceiling() -> int:
    """Derselbe Deckel als gerundete Prioritaet — dieselbe Abbildung wie oben."""
    return max(1, min(10, round(rule_path_raw_ceiling() * 9) + 1))


def rule_path_can_reach_alert_gate(gate_raw: float = ALERT_GATE_RAW) -> bool:
    """Kann ein NUR regelbasiert analysiertes Dokument das Alert-Gate passieren?

    Am 2026-09-10 auf kai-pi5 gemessen: ueber 44.018 regelanalysierte Dokumente
    lag die hoechste Prioritaet bei 6,0 — und alle 11.863 Dokumente mit
    Prioritaet >= 7 stammten ausnahmslos aus ``external_llm``. Die Rechnung hier
    erklaert den Befund, statt ihn nur zu wiederholen: 0.575 gegen ein Gate von
    0.615.
    """
    return rule_path_raw_ceiling() >= gate_raw


def min_priority_as_raw_gate(min_priority: int) -> float:
    """Die Ganzzahl-Schwelle als Schwelle auf ``raw`` — die Umkehrung der Rundung.

    ``round(raw * 9) + 1 >= p``  <=>  ``raw >= (p - 1.5) / 9``.
    Fuer Aufrufer, die eine eigene Schwelle fuehren und trotzdem kontinuierlich
    entscheiden wollen.
    """
    return (min_priority - 1.5) / 9.0


@dataclass(frozen=True)
class PriorityScore:
    priority: int  # 1–10, 10 = most urgent
    raw_score: float  # 0.0–1.0 before rounding
    is_spam_capped: bool
    actionable_bonus_applied: bool


def compute_priority(
    result: AnalysisResult,
    *,
    spam_probability: float = 0.0,
) -> PriorityScore:
    """Compute a priority score (1–10) from an AnalysisResult.

    spam_probability MUST be passed as an explicit parameter — even though
    AnalysisResult carries a spam_probability field, callers must supply it
    separately to make the scoring input auditable and independent of result
    mutation order (apply_to_document() may update result fields in-place).
    Returns a PriorityScore with the integer priority and audit info.
    """
    actionable_value = 1.0 if result.actionable else 0.0
    quality = 1.0 - spam_probability

    raw = (
        result.relevance_score * _W_RELEVANCE
        + result.impact_score * _W_IMPACT
        + result.novelty_score * _W_NOVELTY
        + actionable_value * _W_ACTIONABLE
        + quality * _W_QUALITY
    )

    # Map [0.0, 1.0] → [1, 10]
    priority = max(1, min(10, round(raw * 9) + 1))

    # Actionability bonus
    bonus_applied = result.actionable and priority < 10
    if bonus_applied:
        priority = min(10, priority + 1)

    # Spam cap
    spam_capped = spam_probability > _SPAM_CAP_THRESHOLD
    if spam_capped:
        priority = min(priority, _SPAM_PRIORITY_CAP)

    return PriorityScore(
        priority=priority,
        raw_score=round(raw, 4),
        is_spam_capped=spam_capped,
        actionable_bonus_applied=bonus_applied,
    )


def is_alert_worthy(
    result: AnalysisResult,
    min_priority: int = 7,
    *,
    spam_probability: float = 0.0,
    gate_raw: float | None = None,
) -> bool:
    """Return True if document meets the alert threshold.

    spam_probability must be passed separately — it is not stored on AnalysisResult.
    Spam is always excluded regardless of the threshold.

    ``gate_raw`` entscheidet auf der kontinuierlichen Groesse statt auf der
    gerundeten ``priority`` (siehe ALERT_GATE_RAW). Ohne den Parameter bleibt das
    alte Verhalten unveraendert — Aufrufer, die weiter ganzzahlig denken, merken
    nichts.
    """
    if spam_probability > _SPAM_CAP_THRESHOLD:
        return False
    score = compute_priority(result, spam_probability=spam_probability)
    if gate_raw is not None:
        # Kontinuierlich: die Entscheidung haengt nicht mehr an der Rundung.
        return score.raw_score >= gate_raw
    return score.priority >= min_priority


def calculate_final_relevance(llm_relevance: float, keyword_hits: list[Any]) -> float:
    """Blend LLM relevance score with keyword hit multipliers.

    If document has strong keyword hits, it boosts the LLM base score.
    """
    if not keyword_hits:
        return llm_relevance

    # Calculate keyword density/weight
    # Simple approach: each hit adds 0.05, max +0.3 boost
    boost = min(0.3, len(keyword_hits) * 0.05)

    final_score = llm_relevance + boost
    return min(1.0, final_score)
