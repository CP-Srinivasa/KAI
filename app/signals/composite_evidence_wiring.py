"""Composite-Provider für die orthogonalen Bayes-Extra-Evidenzen (Goal V5 + HYPE-S1).

Der ``SignalGenerator`` nimmt genau EINEN
``bayes_extra_evidences_provider``. Phase 1 verdrahtete dort Funding, Phase 2
fügte Open-Interest hinzu, Phase 3 fügt Long/Short-Ratio hinzu, HYPE-S1 fügt
Sentiment-Überhitzung hinzu — jeweils ohne die früheren Pfade zu brechen.
Lösung: ein dünner Composite, der die je-Phase-Provider baut und ihre
Evidence-Sequenzen in fester Reihenfolge verkettet.

Harte Invariante (kein früherer Pfad regresst)
==============================================
- Keine aktiv ⇒ ``None`` (kein Provider, Generator exakt wie vor Phase 1).
- Genau EINE aktiv ⇒ der **unveränderte** Sub-Provider wird DIREKT
  zurückgegeben (keine Composite-Hülle, kein Verhaltens-Delta gegenüber der
  jeweiligen Phase — byte-identische Evidence + Shadow-Log).
- ≥ 2 aktiv ⇒ ein Composite, der pro Call die Evidenzen in fester Reihenfolge
  Funding → OI → LS → Hype anhängt. Die Bayes-Engine ist gegen die Reihenfolge
  invariant (Produkt der Likelihoods), aber eine feste Reihenfolge hält
  Shadow-Logs/Tests stabil.

Jeder Sub-Provider ist selbst fail-safe (leere Sequenz bei stale/missing).
Der Composite fängt zusätzlich Exceptions je Sub-Provider ab: ein Defekt in
einer Evidenz-Quelle darf die anderen nicht killen und nie den Signal-Pfad.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING

from app.core.domain.document import AnalysisResult
from app.core.evidence_settings import (
    FundingEvidenceSettings,
    HypeEvidenceSettings,
    L2OnChainEvidenceSettings,
    LongShortRatioEvidenceSettings,
    MomentumUniverseEvidenceSettings,
    OpenInterestEvidenceSettings,
)
from app.market_data.models import MarketDataPoint
from app.signals.bayesian_confidence import Evidence
from app.signals.funding_wiring import build_funding_evidence_provider
from app.signals.generator import ExtraEvidencesProvider
from app.signals.hype_wiring import build_hype_evidence_provider
from app.signals.l2_wiring import build_l2_onchain_evidence_provider
from app.signals.ls_wiring import build_ls_evidence_provider
from app.signals.models import SignalDirection
from app.signals.momentum_wiring import build_momentum_evidence_provider
from app.signals.oi_wiring import build_oi_evidence_provider

if TYPE_CHECKING:
    from app.core.settings import AppSettings

logger = logging.getLogger(__name__)


def build_composite_evidence_provider(
    funding_settings: FundingEvidenceSettings,
    oi_settings: OpenInterestEvidenceSettings,
    ls_settings: LongShortRatioEvidenceSettings | None = None,
    hype_settings: HypeEvidenceSettings | None = None,
    l2_settings: L2OnChainEvidenceSettings | None = None,
    momentum_settings: MomentumUniverseEvidenceSettings | None = None,
) -> ExtraEvidencesProvider | None:
    """Baue den kombinierten Extra-Evidences-Provider (oder ``None``).

    Deterministische Reihenfolge der Sub-Provider: Funding → OI → LS → Hype → L2
    → Momentum. Fehlende Settings (Aufrufer früherer Phasen) verhalten sich wie
    off — das Verhalten der jeweils älteren Phasen bleibt unberührt.
    """
    # Reihenfolge fixiert: Funding → OI → LS → Hype → L2 → Momentum (neueste zuletzt).
    candidates: tuple[tuple[str, ExtraEvidencesProvider | None], ...] = (
        ("funding", build_funding_evidence_provider(funding_settings)),
        ("open_interest", build_oi_evidence_provider(oi_settings)),
        (
            "long_short_ratio",
            build_ls_evidence_provider(ls_settings) if ls_settings is not None else None,
        ),
        (
            "sentiment_overheat",
            build_hype_evidence_provider(hype_settings) if hype_settings is not None else None,
        ),
        (
            "l2_onchain",
            build_l2_onchain_evidence_provider(l2_settings) if l2_settings is not None else None,
        ),
        (
            "momentum",
            build_momentum_evidence_provider(momentum_settings)
            if momentum_settings is not None
            else None,
        ),
    )

    active: tuple[tuple[str, ExtraEvidencesProvider], ...] = tuple(
        (name, provider) for name, provider in candidates if provider is not None
    )

    if not active:
        return None
    if len(active) == 1:
        # Genau eine Quelle: den unveränderten Sub-Provider DIREKT durchreichen
        # (keine Composite-Hülle → byte-identisch zum jeweiligen Phasen-Pfad).
        return active[0][1]

    def _composite(
        analysis: AnalysisResult,
        market_data: MarketDataPoint,
        direction: SignalDirection,
    ) -> Sequence[Evidence]:
        out: list[Evidence] = []
        for name, provider in active:
            try:
                out.extend(provider(analysis, market_data, direction))
            except Exception as exc:  # noqa: BLE001 — eine Quelle darf die anderen/Signal nie killen
                logger.warning("[composite-evidence] %s provider raised: %s", name, exc)
        return out

    logger.info(
        "[composite-evidence] %s providers WIRED",
        " + ".join(name for name, _ in active),
    )
    return _composite


def build_composite_evidence_provider_from_settings(
    settings: AppSettings,
) -> ExtraEvidencesProvider | None:
    """Settings-Level-Einstieg für den Trading-Loop (S7-Extraktion, HYPE-S1).

    Bündelt die Auswahl der vier Evidence-Settings-Blöcke an EINER Stelle,
    damit der Loop-Code (God-File, Ratchet D-234) beim nächsten Evidence-
    Layer nicht wieder wächst. Verhalten identisch zu
    ``build_composite_evidence_provider`` mit den vier Blöcken aus
    ``AppSettings``.
    """
    return build_composite_evidence_provider(
        settings.funding_evidence,
        settings.oi_evidence,
        settings.ls_evidence,
        settings.hype_evidence,
        settings.l2_evidence,
        settings.momentum_evidence,
    )


__all__ = [
    "build_composite_evidence_provider",
    "build_composite_evidence_provider_from_settings",
]
