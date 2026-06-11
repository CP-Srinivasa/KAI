"""Composite-Provider für die orthogonalen Bayes-Extra-Evidenzen (Goal V5).

Der ``SignalGenerator`` nimmt genau EINEN
``bayes_extra_evidences_provider``. Phase 1 verdrahtete dort Funding, Phase 2
fügte Open-Interest hinzu, Phase 3 fügt Long/Short-Ratio hinzu — jeweils ohne
die früheren Pfade zu brechen. Lösung: ein dünner Composite, der die je-Phase-
Provider baut und ihre Evidence-Sequenzen in fester Reihenfolge verkettet.

Harte Invariante (kein früherer Pfad regresst)
==============================================
- Keine aktiv ⇒ ``None`` (kein Provider, Generator exakt wie vor Phase 1).
- Genau EINE aktiv ⇒ der **unveränderte** Sub-Provider wird DIREKT
  zurückgegeben (keine Composite-Hülle, kein Verhaltens-Delta gegenüber der
  jeweiligen Phase — byte-identische Evidence + Shadow-Log).
- ≥ 2 aktiv ⇒ ein Composite, der pro Call die Evidenzen in fester Reihenfolge
  Funding → OI → LS anhängt. Die Bayes-Engine ist gegen die Reihenfolge
  invariant (Produkt der Likelihoods), aber eine feste Reihenfolge hält
  Shadow-Logs/Tests stabil.

Jeder Sub-Provider ist selbst fail-safe (leere Sequenz bei stale/missing).
Der Composite fängt zusätzlich Exceptions je Sub-Provider ab: ein Defekt in
einer Evidenz-Quelle darf die anderen nicht killen und nie den Signal-Pfad.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from app.core.domain.document import AnalysisResult
from app.core.settings import (
    FundingEvidenceSettings,
    LongShortRatioEvidenceSettings,
    OpenInterestEvidenceSettings,
)
from app.market_data.models import MarketDataPoint
from app.signals.bayesian_confidence import Evidence
from app.signals.funding_wiring import build_funding_evidence_provider
from app.signals.generator import ExtraEvidencesProvider
from app.signals.ls_wiring import build_ls_evidence_provider
from app.signals.models import SignalDirection
from app.signals.oi_wiring import build_oi_evidence_provider

logger = logging.getLogger(__name__)


def build_composite_evidence_provider(
    funding_settings: FundingEvidenceSettings,
    oi_settings: OpenInterestEvidenceSettings,
    ls_settings: LongShortRatioEvidenceSettings | None = None,
) -> ExtraEvidencesProvider | None:
    """Baue den kombinierten Extra-Evidences-Provider (oder ``None``).

    Deterministische Reihenfolge der Sub-Provider: Funding → OI → LS. Ein
    fehlendes ``ls_settings`` (Aufrufer vor Phase 3) verhält sich wie L/S-off
    — Phase-1/2-Verhalten bleibt unberührt.
    """
    # Reihenfolge fixiert: Funding zuerst, dann OI, dann LS.
    candidates: tuple[tuple[str, ExtraEvidencesProvider | None], ...] = (
        ("funding", build_funding_evidence_provider(funding_settings)),
        ("open_interest", build_oi_evidence_provider(oi_settings)),
        (
            "long_short_ratio",
            build_ls_evidence_provider(ls_settings) if ls_settings is not None else None,
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


__all__ = ["build_composite_evidence_provider"]
