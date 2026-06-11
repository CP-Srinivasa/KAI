"""Composite-Provider für die orthogonalen Bayes-Extra-Evidenzen (Goal V5).

Der ``SignalGenerator`` nimmt genau EINEN
``bayes_extra_evidences_provider``. Phase 1 verdrahtete dort den Funding-
Provider. Phase 2 fügt Open-Interest hinzu — ohne den Funding-Pfad zu
brechen. Lösung: ein dünner Composite, der die je-Phase-Provider baut und
ihre Evidence-Sequenzen verkettet.

Harte Invariante (Funding nicht regressen)
==========================================
- Beide aus  ⇒ ``None`` (kein Provider, Generator exakt wie vor Phase 1).
- Nur Funding ⇒ der **unveränderte** Funding-Provider wird DIREKT
  zurückgegeben (keine Composite-Hülle, kein Verhaltens-Delta gegenüber
  Phase 1 — byte-identische Evidence + Shadow-Log).
- Nur OI      ⇒ der OI-Provider wird direkt zurückgegeben.
- Beide an    ⇒ ein Composite, der pro Call zuerst Funding-, dann OI-
  Evidence anhängt. Reihenfolge ist deterministisch (Funding zuerst), die
  Bayes-Engine ist gegen die Reihenfolge invariant (Produkt der Likelihoods),
  aber eine feste Reihenfolge hält Shadow-Logs/Tests stabil.

Jeder Sub-Provider ist selbst fail-safe (leere Sequenz bei stale/missing).
Der Composite fängt zusätzlich Exceptions je Sub-Provider ab: ein Defekt in
einer Evidenz-Quelle darf die andere nicht killen und nie den Signal-Pfad.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from app.core.domain.document import AnalysisResult
from app.core.settings import FundingEvidenceSettings, OpenInterestEvidenceSettings
from app.market_data.models import MarketDataPoint
from app.signals.bayesian_confidence import Evidence
from app.signals.funding_wiring import build_funding_evidence_provider
from app.signals.generator import ExtraEvidencesProvider
from app.signals.models import SignalDirection
from app.signals.oi_wiring import build_oi_evidence_provider

logger = logging.getLogger(__name__)


def build_composite_evidence_provider(
    funding_settings: FundingEvidenceSettings,
    oi_settings: OpenInterestEvidenceSettings,
) -> ExtraEvidencesProvider | None:
    """Baue den kombinierten Extra-Evidences-Provider (oder ``None``)."""
    funding_provider = build_funding_evidence_provider(funding_settings)
    oi_provider = build_oi_evidence_provider(oi_settings)

    if funding_provider is None and oi_provider is None:
        return None
    if oi_provider is None:
        # Nur Funding: Phase-1-Pfad unverändert durchreichen.
        return funding_provider
    if funding_provider is None:
        return oi_provider

    # Beide an: deterministisch Funding zuerst, dann OI.
    sub_providers: tuple[tuple[str, ExtraEvidencesProvider], ...] = (
        ("funding", funding_provider),
        ("open_interest", oi_provider),
    )

    def _composite(
        analysis: AnalysisResult,
        market_data: MarketDataPoint,
        direction: SignalDirection,
    ) -> Sequence[Evidence]:
        out: list[Evidence] = []
        for name, provider in sub_providers:
            try:
                out.extend(provider(analysis, market_data, direction))
            except Exception as exc:  # noqa: BLE001 — eine Quelle darf die andere/Signal nie killen
                logger.warning("[composite-evidence] %s provider raised: %s", name, exc)
        return out

    logger.info("[composite-evidence] funding + open-interest providers WIRED")
    return _composite


__all__ = ["build_composite_evidence_provider"]
