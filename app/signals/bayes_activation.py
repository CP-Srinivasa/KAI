"""Verdrahtungs-Helfer für die Bayesian Confidence Engine.

Eine Stelle, an der Settings → SignalGenerator-Kwargs übersetzt werden.
Das hält ``build_trading_loop`` schlank und macht den Schalterzustand
testbar, ohne die ganze Loop hochzufahren.

Vertrag:
  - Engine = ``None`` wenn ``settings.risk.bayes_confidence_enabled`` False.
    Generator verhält sich dann exakt wie vor der Bayes-Integration.
  - Engine ≠ ``None`` und Audit-Pfad gesetzt sobald der Flag True ist.
    Der Operator kann zusätzlich ``bayes_confidence_shadow_only=False``
    setzen, um harte Gates scharf zu schalten — der Helper reicht alle
    drei Schwellwerte unverändert durch.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.core.settings import RiskSettings
from app.market_data.regime_detection import RegimeDetectionEngine
from app.signals.bayes_journal import DEFAULT_BAYES_AUDIT_PATH
from app.signals.bayesian_confidence import (
    BayesianConfidenceEngine,
    build_default_engine,
)
from app.signals.generator import ExtraEvidencesProvider


def build_bayes_signal_kwargs(
    risk_settings: RiskSettings,
    *,
    audit_path: Path | str | None = None,
    extra_evidences_provider: ExtraEvidencesProvider | None = None,
    engine: BayesianConfidenceEngine | None = None,
    regime_engine: RegimeDetectionEngine | None = None,
) -> Mapping[str, Any]:
    """Liefere die Bayes-spezifischen Kwargs für ``SignalGenerator``.

    Wenn der Flag aus ist, ist das Ergebnis ein leeres Mapping —
    ``SignalGenerator(**kwargs)`` läuft dann mit den Legacy-Defaults und
    behält das exakt vorherige Verhalten.

    Wenn der Flag an ist:
      - ``bayes_engine`` wird gesetzt (Default: ``build_default_engine()``).
      - ``bayes_audit_path`` wird gesetzt (Default: ``DEFAULT_BAYES_AUDIT_PATH``).
      - ``bayes_shadow_only`` / ``min_bayes_confidence`` /
        ``max_bayes_uncertainty`` werden 1:1 aus den Settings übernommen.
      - ``bayes_extra_evidences_provider`` wird angefügt, wenn gesetzt.
    """
    if not risk_settings.bayes_confidence_enabled:
        return {}

    resolved_engine = engine if engine is not None else build_default_engine()
    resolved_path = Path(audit_path) if audit_path is not None else DEFAULT_BAYES_AUDIT_PATH

    kwargs: dict[str, Any] = {
        "bayes_engine": resolved_engine,
        "bayes_shadow_only": risk_settings.bayes_confidence_shadow_only,
        "min_bayes_confidence": risk_settings.min_bayes_confidence,
        "max_bayes_uncertainty": risk_settings.max_bayes_uncertainty,
        "bayes_audit_path": resolved_path,
    }
    if extra_evidences_provider is not None:
        kwargs["bayes_extra_evidences_provider"] = extra_evidences_provider
    if regime_engine is not None:
        kwargs["regime_engine"] = regime_engine
    return kwargs


__all__ = ["build_bayes_signal_kwargs"]
